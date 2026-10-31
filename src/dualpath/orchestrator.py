"""双线路并行与共同汇聚的编排器（legacy run 模式，v0.3 将移除）。"""

from __future__ import annotations

import asyncio

from .llm import DualPathError, LanguageModel, ModelError
from .models import ErrorPathResult, RunResult, utc_now
from .paths import DirectPath, ErrorPath, as_json
from .prompts import FINAL_MERGE
from .reporting import Reporter
from .search import SearchProvider


class DualPath:
    def __init__(
        self,
        model: LanguageModel,
        search: SearchProvider,
        reporter: Reporter | None = None,
        max_problems: int = 5,
        search_results: int = 4,
    ) -> None:
        self.model = model
        self.direct = DirectPath(model)
        self.error = ErrorPath(
            model=model,
            search=search,
            max_problems=max_problems,
            search_results=search_results,
        )
        self.reporter = reporter or Reporter()

    async def run(self, task: str) -> RunResult:
        task = task.strip()
        if not task:
            raise ValueError("任务不能为空")
        started_at = utc_now()
        self.reporter.run_started(task)

        # 两条线路从任务开始就真实并行。任何一条失败都不再直接毁掉整次运行：
        # 线路一失败没有可交付结果；线路二失败则降级为“只用草稿”。
        direct_future = asyncio.create_task(self._run_direct(task))
        error_future = asyncio.create_task(self.error.run(task))
        direct_raw, error_raw = await asyncio.gather(
            direct_future, error_future, return_exceptions=True
        )

        if isinstance(direct_raw, BaseException):
            if isinstance(direct_raw, asyncio.CancelledError):
                raise direct_raw
            raise ModelError(f"线路一失败：{type(direct_raw).__name__}") from None
        direct_output = direct_raw

        if isinstance(error_raw, BaseException):
            if isinstance(error_raw, asyncio.CancelledError):
                raise error_raw
            error_result = ErrorPathResult(
                output="",
                problems=[],
                failures=[{"stage": "error_path", "error_type": type(error_raw).__name__}],
            )
        else:
            error_result = error_raw
        failures = list(error_result.failures)
        self.reporter.problems_ready(error_result.problems)

        direct_convergence = ""
        error_convergence = ""
        status = "completed"
        try:
            if not error_result.problems:
                # 错误线路没有可用问题时，不为汇聚额外付费，直接采用草稿。
                final_output = direct_output
                status = "degraded"
            else:
                self.reporter.convergence_started()
                direct_convergence = await self.direct.converge(
                    task=task,
                    output=direct_output,
                    error=error_result,
                )
                error_convergence = await self.error.converge(
                    task=task,
                    output=error_result.output,
                    problems=error_result.problems,
                    direct_convergence=direct_convergence,
                )
                final_output = await self.model.complete(
                    FINAL_MERGE,
                    as_json(
                        {
                            "task": task,
                            "direct_path_contribution": direct_convergence,
                            "error_path_contribution": error_convergence,
                        }
                    ),
                )
        except DualPathError as error:
            failures.append({"stage": "convergence", "error_type": type(error).__name__})
            direct_convergence = ""
            error_convergence = ""
            final_output = direct_output
            status = "degraded"
        if failures:
            # 只要任一阶段有失败，就把整次运行标成降级：结果可用，但覆盖不完整。
            status = "degraded"
        finished_at = utc_now()
        self.reporter.run_completed(status)
        return RunResult(
            task=task,
            direct_output=direct_output,
            error_output=error_result.output,
            direct_convergence=direct_convergence,
            error_convergence=error_convergence,
            final_output=final_output,
            problems=error_result.problems,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            failures=failures,
        )

    async def _run_direct(self, task: str) -> str:
        output = await self.direct.run(task)
        self.reporter.direct_completed()
        return output
