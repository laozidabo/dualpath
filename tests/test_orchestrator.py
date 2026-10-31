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


# ---------------------------------------------------------------------------
# v0.2 新增：双线路降级容错（全部离线；不写 sleep，不访问网络）
# ---------------------------------------------------------------------------


class DegradedFakeModel(LanguageModel):
    """按提示词阶段返回固定内容，并可在指定阶段抛出异常。"""

    def __init__(
        self,
        *,
        fail_on: str | None = None,
        error: BaseException | None = None,
        partial_failure: bool = False,
        invalid_discover: bool = False,
    ) -> None:
        self.fail_on = fail_on
        self.error = error
        self.partial_failure = partial_failure
        self.invalid_discover = invalid_discover
        self.calls: list[str] = []

    async def complete(self, system: str, user: str) -> str:
        self.calls.append(system)
        if "[DIRECT_EXECUTE]" in system:
            if self.fail_on == "direct":
                raise self.error
            return "直接线路输出"
        if "[ERROR_DISCOVER]" in system:
            if self.invalid_discover:
                return "这不是 JSON"
            return json.dumps(
                [
                    {"title": "甲问题", "description": "说明甲", "exploration": "推演甲"},
                    {"title": "乙问题", "description": "说明乙", "exploration": "推演乙"},
                ],
                ensure_ascii=False,
            )
        if "[ERROR_PROBABILITY]" in system:
            if self.partial_failure and "甲问题" in user:
                raise self.error
            return json.dumps(
                {"probability": 0.6, "reasoning": "测试判断", "basis": "model_judgment"},
                ensure_ascii=False,
            )
        if "[ERROR_SUMMARY]" in system:
            return "错误线路总结"
        if "[DIRECT_CONVERGE]" in system:
            if self.fail_on == "converge":
                raise self.error
            return "线路一汇聚结果"
        if "[ERROR_CONVERGE]" in system:
            return "线路二汇聚结果"
        if "[FINAL_MERGE]" in system:
            return "最终合并结果"
        raise AssertionError(f"未知提示词：{system}")


async def test_partial_investigation_failure_keeps_converging_but_marks_degraded() -> None:
    from dualpath.llm import ModelError

    model = DegradedFakeModel(partial_failure=True, error=ModelError("某个问题调查失败"))
    engine = DualPath(model=model, search=NoSearch(), max_problems=2)

    result = await engine.run("完成一个测试任务")

    assert result.status == "degraded"
    assert result.final_output == "最终合并结果"
    assert [problem.title for problem in result.problems] == ["乙问题"]
    assert len(result.failures) == 1
    assert result.failures[0]["stage"] == "error_investigate"
    assert result.failures[0]["problem"] == "P001"
    assert len(model.calls) == 8


async def test_invalid_discover_json_degrades_whole_run_to_direct_output() -> None:
    model = DegradedFakeModel(invalid_discover=True)
    engine = DualPath(model=model, search=NoSearch(), max_problems=2)

    result = await engine.run("完成一个测试任务")

    assert result.status == "degraded"
    assert result.final_output == "直接线路输出"
    assert result.problems == []
    assert len(result.failures) == 1
    assert result.failures[0]["stage"] == "error_path"
    assert not any("[FINAL_MERGE]" in system for system in model.calls)


async def test_direct_path_failure_raises_model_error() -> None:
    import pytest

    from dualpath.llm import ModelError

    model = DegradedFakeModel(fail_on="direct", error=ModelError("直接线路失败"))
    engine = DualPath(model=model, search=NoSearch(), max_problems=2)

    with pytest.raises(ModelError) as excinfo:
        await engine.run("完成一个测试任务")

    assert "线路一失败" in str(excinfo.value)


async def test_convergence_failure_degrades_to_direct_output() -> None:
    from dualpath.llm import ModelError

    model = DegradedFakeModel(fail_on="converge", error=ModelError("汇聚失败"))
    engine = DualPath(model=model, search=NoSearch(), max_problems=2)

    result = await engine.run("完成一个测试任务")

    assert result.status == "degraded"
    assert result.final_output == "直接线路输出"
    assert result.direct_convergence == ""
    assert result.error_convergence == ""
    assert len(result.failures) == 1
    assert result.failures[0]["stage"] == "convergence"


def test_run_result_serialization_includes_status_and_failures() -> None:
    from dualpath.models import Problem, RunResult

    problem = Problem(
        id="P001",
        title="问题",
        description="说明",
        exploration="推演",
        probability=0.5,
        reasoning="理由",
        basis="model_judgment",
    )
    result = RunResult(
        task="任务",
        direct_output="直接",
        error_output="错误",
        direct_convergence="",
        error_convergence="",
        final_output="最终",
        problems=[problem],
        started_at="2026-10-31T00:00:00+00:00",
        finished_at="2026-10-31T00:00:01+00:00",
        status="degraded",
        failures=[{"stage": "convergence", "error_type": "ModelError"}],
    )

    payload = result.to_dict()
    assert payload["status"] == "degraded"
    assert payload["failures"] == [{"stage": "convergence", "error_type": "ModelError"}]

    markdown = result.to_markdown()
    assert "- 状态：degraded" in markdown
    assert "## 降级运行记录" in markdown
    assert "stage=convergence" in markdown
