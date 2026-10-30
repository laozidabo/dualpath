import asyncio
import json

from dualpath.llm import LanguageModel
from dualpath.orchestrator import DualPath
from dualpath.paths import ErrorPath
from dualpath.search import NoSearch, SearchProvider, SearchResult


class FakeModel(LanguageModel):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []

    async def complete(self, system: str, user: str) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(system)
        await asyncio.sleep(0.02)
        try:
            if "[DIRECT_EXECUTE]" in system:
                return "线路一的直接结果"
            if "[ERROR_DISCOVER]" in system:
                return json.dumps(
                    [
                        {
                            "title": "高概率问题",
                            "description": "第一条错误路线",
                            "exploration": "沿第一条错误路线推演",
                        },
                        {
                            "title": "低概率问题",
                            "description": "第二条错误路线",
                            "exploration": "沿第二条错误路线推演",
                        },
                    ],
                    ensure_ascii=False,
                )
            if "[ERROR_PROBABILITY]" in system:
                high = "高概率问题" in user
                return json.dumps(
                    {
                        "probability": 0.8 if high else 0.3,
                        "reasoning": "测试判断",
                        "basis": "internet",
                    }
                )
            if "[ERROR_SUMMARY]" in system:
                return "线路二的错误路线总结"
            if "[DIRECT_CONVERGE]" in system:
                return "线路一吸收错误路线后的结果"
            if "[ERROR_CONVERGE]" in system:
                return "线路二提交的最终贡献"
            if "[FINAL_MERGE]" in system:
                return "两条线路共同完成的最终结果"
            raise AssertionError(f"未知提示词：{system}")
        finally:
            self.active -= 1


class FakeSearch(SearchProvider):
    async def search(self, query: str, max_results: int = 4) -> list[SearchResult]:
        await asyncio.sleep(0.01)
        return [
            SearchResult(
                title="真实检索结果",
                url="https://example.com/evidence",
                snippet="用于测试的资料摘要",
            )
        ]


async def test_two_paths_run_in_parallel_and_converge() -> None:
    model = FakeModel()
    engine = DualPath(model=model, search=FakeSearch(), max_problems=2)

    result = await engine.run("完成一个测试任务")

    assert model.max_active >= 2
    assert result.final_output == "两条线路共同完成的最终结果"
    assert [item.level for item in result.problems] == [1, 2]
    assert result.problems[0].basis == "internet"
    assert result.problems[0].evidence[0].url == "https://example.com/evidence"
    assert "一级问题" not in result.to_markdown()
    assert "1级问题：高概率问题" in result.to_markdown()


async def test_no_search_results_force_model_judgment() -> None:
    model = FakeModel()
    path = ErrorPath(model=model, search=NoSearch(), max_problems=2)

    result = await path.run("完成一个测试任务")

    assert all(item.basis == "model_judgment" for item in result.problems)
    assert all(not item.evidence for item in result.problems)
