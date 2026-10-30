"""双线路并行与共同汇聚的编排器。"""

from __future__ import annotations

import asyncio

from .llm import LanguageModel
from .models import RunResult, utc_now
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

        # 两条线路从任务开始就真实并行，而不是先执行再检查。
        direct_future = asyncio.create_task(self._run_direct(task))
        error_future = asyncio.create_task(self.error.run(task))
        direct_output, error_result = await asyncio.gather(direct_future, error_future)
        self.reporter.problems_ready(error_result.problems)

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
        finished_at = utc_now()
        self.reporter.run_completed()
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
        )

    async def _run_direct(self, task: str) -> str:
        output = await self.direct.run(task)
        self.reporter.direct_completed()
        return output
