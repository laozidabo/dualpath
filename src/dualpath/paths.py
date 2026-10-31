"""直接解决线路与错误路线。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .llm import DualPathError, LanguageModel, complete_json
from .models import ErrorPathResult, Evidence, Problem, classify_problems
from .prompts import (
    DIRECT_CONVERGE,
    DIRECT_EXECUTE,
    ERROR_CONVERGE,
    ERROR_DISCOVER,
    ERROR_PROBABILITY,
    ERROR_SUMMARY,
)
from .search import SearchProvider, SearchResult


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class DirectPath:
    """线路一：始终直接解决任务。"""

    def __init__(self, model: LanguageModel) -> None:
        self.model = model

    async def run(self, task: str) -> str:
        return await self.model.complete(DIRECT_EXECUTE, f"用户任务：\n{task}")

    async def converge(self, task: str, output: str, error: ErrorPathResult) -> str:
        payload = {
            "task": task,
            "direct_output": output,
            "error_path_output": error.output,
            "problems": [problem.to_dict() for problem in error.problems],
        }
        return await self.model.complete(DIRECT_CONVERGE, as_json(payload))


class ErrorPath:
    """线路二：探索错误路线，并按概率给问题分级。"""

    def __init__(
        self,
        model: LanguageModel,
        search: SearchProvider,
        max_problems: int = 5,
        search_results: int = 4,
    ) -> None:
        if max_problems < 1:
            raise ValueError("max_problems 必须至少为 1")
        self.model = model
        self.search = search
        self.max_problems = max_problems
        self.search_results = search_results

    async def run(self, task: str) -> ErrorPathResult:
        candidates = await self._discover(task)
        investigated = await asyncio.gather(
            *(self._investigate(task, candidate) for candidate in candidates),
            return_exceptions=True,
        )
        problems: list[Problem] = []
        failures: list[dict[str, str]] = []
        for candidate, outcome in zip(candidates, investigated, strict=True):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                failures.append(
                    {
                        "stage": "error_investigate",
                        "problem": candidate.id,
                        "title": candidate.title,
                        "error_type": type(outcome).__name__,
                    }
                )
            else:
                problems.append(outcome)
        if not problems:
            return ErrorPathResult(output="", problems=[], failures=failures)
        ordered = classify_problems(problems)
        summary_payload = {
            "task": task,
            "instruction": "总结线路二走过的错误路线。",
            "problems": [problem.to_dict() for problem in ordered],
        }
        try:
            output = await self.model.complete(ERROR_SUMMARY, as_json(summary_payload))
        except DualPathError as error:
            failures.append({"stage": "error_summary", "error_type": type(error).__name__})
            output = ""
        return ErrorPathResult(output=output, problems=ordered, failures=failures)

    async def converge(
        self,
        task: str,
        output: str,
        problems: list[Problem],
        direct_convergence: str,
    ) -> str:
        payload = {
            "task": task,
            "error_path_output": output,
            "problems": [problem.to_dict() for problem in problems],
            "direct_path_convergence": direct_convergence,
        }
        return await self.model.complete(ERROR_CONVERGE, as_json(payload))

    async def _discover(self, task: str) -> list[Problem]:
        prompt = f"用户任务：\n{task}\n\n问题数量上限：{self.max_problems}"
        raw = await complete_json(self.model, ERROR_DISCOVER, prompt)
        if not isinstance(raw, list):
            raise ValueError("错误线路的发现阶段必须返回 JSON 数组")

        candidates: list[Problem] = []
        for index, item in enumerate(raw[: self.max_problems], start=1):
            if not isinstance(item, dict) or not item.get("title"):
                continue
            candidates.append(
                Problem(
                    id=f"P{index:03d}",
                    title=str(item["title"]),
                    description=str(item.get("description", "")),
                    exploration=str(item.get("exploration", "")),
                    probability=0.0,
                    reasoning="",
                    basis="model_judgment",
                )
            )
        if not candidates:
            raise ValueError("错误线路没有返回可用的问题")
        return candidates

    async def _investigate(self, task: str, candidate: Problem) -> Problem:
        results: list[SearchResult] = []
        search_failed = False
        query = f"{task[:240]} {candidate.title} 出现原因 概率 发生率"
        try:
            results = await self.search.search(query, max_results=self.search_results)
        except Exception:
            # 搜索不可用时仍按原始设计，由 AI 自行判断并明确标注。
            search_failed = True

        evidence_payload = [
            {"title": item.title, "url": item.url, "snippet": item.snippet}
            for item in results
        ]
        prompt = as_json(
            {
                "task": task,
                "problem": {
                    "title": candidate.title,
                    "description": candidate.description,
                    "exploration": candidate.exploration,
                },
                "internet_evidence": evidence_payload,
                "has_internet_evidence": bool(results),
            }
        )
        raw = await complete_json(self.model, ERROR_PROBABILITY, prompt)
        if not isinstance(raw, dict):
            raise ValueError("概率判断阶段必须返回 JSON 对象")
        try:
            probability = float(raw["probability"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("概率判断缺少有效的 probability 数值") from error
        if not 0.0 <= probability <= 1.0:
            raise ValueError("问题概率必须位于 0.0 到 1.0 之间")

        basis = str(raw.get("basis", ""))
        if not results:
            basis = "model_judgment"
        elif basis not in {"internet", "mixed"}:
            basis = "mixed"
        reasoning = str(raw.get("reasoning", ""))
        if search_failed:
            reasoning = f"互联网搜索未成功，本次改由AI自行判断。{reasoning}"

        candidate.probability = probability
        candidate.reasoning = reasoning
        candidate.basis = basis
        candidate.evidence = [
            Evidence(title=item.title, url=item.url, snippet=item.snippet)
            for item in results
        ]
        return candidate
