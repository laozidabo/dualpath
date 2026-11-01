"""v0.2 新增：review 模式的失败清单、盲审、修正与降级状态（全部离线）。"""

import json

import pytest

from dualpath.llm import LanguageModel, ModelError, ModelRequestRejected, ModelResponseError, ModelTimeout
from dualpath.models import Finding, ReviewResult
from dualpath.review import ReviewEngine

QUOTE = "用户必须在三秒内完成提交"
TASK = f"{QUOTE}，否则视为失败。"

_STAGE_MARKERS = (
    ("[REVIEW_DRAFT]", "draft"),
    ("[REVIEW_PROBE]", "probe"),
    ("[REVIEW_DECISIONS]", "decisions"),
    ("[REVIEW_FINAL]", "final"),
)


def make_finding(finding_id: str = "F1", **overrides) -> dict:
    row = {
        "id": finding_id,
        "title": "超时未处理",
        "mechanism": "接口不处理超时，任务被静默丢弃",
        "trigger": "提交耗时超过三秒",
        "rule_quote": QUOTE,
        "check_kind": "executable",
        "check": "构造耗时四秒的提交并观察结果",
        "expected_signal": "返回明确失败提示",
    }
    row.update(overrides)
    return row


def findings_payload(*rows: dict) -> str:
    return json.dumps({"findings": list(rows)}, ensure_ascii=False)


def make_decision(finding_id: str = "F1", verdict: str = "accept", reason: str = "原文支持") -> dict:
    return {"id": finding_id, "verdict": verdict, "reason": reason}


def decisions_payload(*rows: dict) -> str:
    return json.dumps({"decisions": list(rows)}, ensure_ascii=False)


class FakeReviewModel(LanguageModel):
    """按提示词阶段返回预设内容；errors 支持逐次消费的异常序列。"""

    def __init__(
        self,
        *,
        draft: str = "草稿结果",
        probe: str | list[str] | None = None,
        decisions: str | None = None,
        final: str = "修正后的最终结果",
        errors: dict[str, list[BaseException | None]] | None = None,
    ) -> None:
        self.draft = draft
        self.probe_responses = list(probe) if isinstance(probe, (list, tuple)) else [probe]
        self.decisions = decisions if decisions is not None else decisions_payload(make_decision())
        self.final = final
        self.errors = {stage: list(sequence) for stage, sequence in (errors or {}).items()}
        self.calls: list[str] = []
        self.systems: list[str] = []
        self.user_prompts: list[str] = []

    @property
    def stage_counts(self) -> dict[str, int]:
        counts = {stage: 0 for _, stage in _STAGE_MARKERS}
        for stage in self.calls:
            counts[stage] += 1
        return counts

    def _consume_error(self, stage: str) -> BaseException | None:
        sequence = self.errors.get(stage)
        if not sequence:
            return None
        self.errors[stage] = sequence[1:]
        return sequence[0]

    def _next_probe(self) -> str:
        if len(self.probe_responses) > 1:
            return self.probe_responses.pop(0)
        return self.probe_responses[0]

    @staticmethod
    def _stage_for(system: str) -> str:
        for marker, stage in _STAGE_MARKERS:
            if marker in system:
                return stage
        raise AssertionError(f"未知提示词：{system}")

    async def complete(self, system: str, user: str) -> str:
        stage = self._stage_for(system)
        self.calls.append(stage)
        self.systems.append(system)
        self.user_prompts.append(user)
        error = self._consume_error(stage)
        if error is not None:
            raise error
        if stage == "draft":
            return self.draft
        if stage == "probe":
            return self._next_probe()
        if stage == "decisions":
            return self.decisions
        return self.final


async def test_review_completed_flow_uses_four_calls() -> None:
    model = FakeReviewModel(
        probe=findings_payload(make_finding()),
        decisions=decisions_payload(make_decision(reason="原文支持且检查可执行")),
    )

    result = await ReviewEngine(model).run(TASK)

    assert result.status == "completed"
    assert result.model_calls == 4
    assert model.stage_counts == {"draft": 1, "probe": 1, "decisions": 1, "final": 1}
    assert result.draft == "草稿结果"
    assert result.final_output == "修正后的最终结果"
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.id == "F1"
    assert finding.status == "supported"
    assert finding.review_reason == "原文支持且检查可执行"
    assert finding.rule_source == "task"
    assert "supported_findings" in model.user_prompts[-1]


async def test_all_rejected_findings_return_draft_only_without_final_call() -> None:
    model = FakeReviewModel(
        probe=findings_payload(make_finding()),
        decisions=decisions_payload(make_decision(verdict="reject", reason="检查项不可用")),
    )

    result = await ReviewEngine(model).run(TASK)

    assert result.status == "draft_only"
    assert result.final_output == result.draft == "草稿结果"
    assert result.model_calls == 3
    assert model.stage_counts["final"] == 0
    assert result.findings[0].status == "rejected"


async def test_empty_findings_returns_no_findings_without_retry() -> None:
    model = FakeReviewModel(probe=findings_payload())

    result = await ReviewEngine(model).run(TASK)

    assert result.status == "no_findings"
    assert result.final_output == result.draft == "草稿结果"
    assert result.model_calls == 2
    assert model.stage_counts == {"draft": 1, "probe": 1, "decisions": 0, "final": 0}


async def test_probe_retries_once_after_invalid_json() -> None:
    model = FakeReviewModel(probe=["不是 JSON", findings_payload(make_finding())])

    result = await ReviewEngine(model).run(TASK)

    assert result.status == "completed"
    assert result.model_calls == 5
    probe_systems = [system for system in model.systems if "[REVIEW_PROBE]" in system]
    assert len(probe_systems) == 2
    assert "[格式重试]" in probe_systems[1]


async def test_quote_not_from_source_fails_probe_after_retry() -> None:
    model = FakeReviewModel(probe=findings_payload(make_finding(rule_quote="这条规则并没有出现在任务或资料中")))

    result = await ReviewEngine(model).run(TASK)

    assert result.status == "probe_failed"
    assert result.final_output == result.draft == "草稿结果"
    assert result.model_calls == 3
    assert model.stage_counts["probe"] == 2
    assert model.stage_counts["decisions"] == 0


async def test_structural_findings_errors_trigger_probe_failed() -> None:
    invalid_payloads = [
        findings_payload(make_finding("F1"), make_finding("F1")),  # 重复 id
        findings_payload(*(make_finding(f"F{index}") for index in range(1, 7))),  # 超过 max_findings
        json.dumps(
            {"findings": [{key: value for key, value in make_finding().items() if key != "check"}]},
            ensure_ascii=False,
        ),  # 缺字段
        findings_payload(make_finding(check_kind="automatic")),  # check_kind 非法
    ]

    for payload in invalid_payloads:
        model = FakeReviewModel(probe=payload)
        result = await ReviewEngine(model).run(TASK)

        assert result.status == "probe_failed", payload
        assert result.final_output == result.draft == "草稿结果"
        assert model.stage_counts["probe"] == 2


async def test_invalid_decisions_trigger_review_failed_without_retry() -> None:
    invalid_payloads = [
        decisions_payload(),  # 缺 id
        decisions_payload(make_decision("F1"), make_decision("F2")),  # 多余 id
        decisions_payload(make_decision(verdict="maybe")),  # verdict 非法
    ]

    for payload in invalid_payloads:
        model = FakeReviewModel(probe=findings_payload(make_finding()), decisions=payload)
        result = await ReviewEngine(model).run(TASK)

        assert result.status == "review_failed", payload
        assert result.final_output == result.draft == "草稿结果"
        assert model.stage_counts["decisions"] == 1
        assert model.stage_counts["final"] == 0


async def test_quote_from_context_file_sets_rule_source() -> None:
    context = {"spec.md": f"规范：{QUOTE}，超时即失败。"}
    model = FakeReviewModel(probe=findings_payload(make_finding()))

    result = await ReviewEngine(model).run("按规范完成提交。", context=context)

    assert result.status == "completed"
    assert result.findings[0].rule_source == "spec.md"


async def test_all_stages_receive_full_context_without_leaking_draft_to_blind_stages() -> None:
    context = {"spec.md": f"{QUOTE}。例外：离线提交允许排队。", "notes.md": "保留已有数据。"}
    model = FakeReviewModel(probe=findings_payload(make_finding()))
    result = await ReviewEngine(model).run("依照附件规范实施。", context=context)
    assert result.status == "completed"
    for stage, text in zip(model.calls, model.user_prompts, strict=True):
        payload = json.loads(text)
        assert payload["context"] == context
        if stage in {"probe", "decisions"}:
            assert "draft" not in payload
            assert model.draft not in text


@pytest.mark.parametrize("stage", ["draft", "final"])
async def test_empty_deliverable_is_not_reported_as_completed(stage) -> None:
    model = FakeReviewModel(probe=findings_payload(make_finding()), **{stage: " \n "})
    result = await ReviewEngine(model).run(TASK)
    assert result.status == ("model_failed" if stage == "draft" else "review_failed")
    assert result.final_output == ("" if stage == "draft" else model.draft)
    if stage == "draft":
        assert model.stage_counts["decisions"] == 0


@pytest.mark.parametrize("stage", ["decisions", "final"])
@pytest.mark.parametrize("error_type", [ModelError, ModelTimeout, ModelRequestRejected])
async def test_upstream_failures_preserve_draft(stage, error_type) -> None:
    model = FakeReviewModel(
        probe=findings_payload(make_finding()), errors={stage: [error_type("接口不可用")]},
    )
    result = await ReviewEngine(model).run(TASK)
    assert result.status == "review_failed"
    assert result.final_output == model.draft
    assert model.stage_counts[stage] == 1


@pytest.mark.parametrize("value", [[], {}, None, 3])
async def test_wrong_enum_types_follow_protocol_failure_paths(value) -> None:
    model = FakeReviewModel(probe=[
        findings_payload(make_finding(check_kind=value)), findings_payload(make_finding()),
    ])
    result = await ReviewEngine(model).run(TASK)
    assert result.status == "completed"
    assert model.stage_counts["probe"] == 2
    model = FakeReviewModel(
        probe=findings_payload(make_finding()), decisions=decisions_payload(make_decision(verdict=value)),
    )
    result = await ReviewEngine(model).run(TASK)
    assert result.status == "review_failed"
    assert result.final_output == model.draft


def test_max_findings_out_of_range_raises_value_error() -> None:
    for value in (0, 11):
        with pytest.raises(ValueError):
            ReviewEngine(FakeReviewModel(), max_findings=value)


def _finding_with_status(finding_id: str, status: str) -> Finding:
    return Finding(
        id=finding_id,
        title="标题",
        mechanism="机理",
        trigger="触发",
        rule_quote=QUOTE,
        check_kind="manual",
        check="人工确认",
        expected_signal="信号",
        status=status,
        review_reason="理由",
    )


def test_markdown_has_status_groups_and_no_probability_wording() -> None:
    result = ReviewResult(
        task=TASK,
        draft="草稿",
        findings=[
            _finding_with_status("F1", "supported"),
            _finding_with_status("F2", "ambiguous"),
            _finding_with_status("F3", "rejected"),
            _finding_with_status("F4", "pending"),
        ],
        final_output="最终",
        status="completed",
        model_calls=4,
        started_at="2026-10-31T00:00:00+00:00",
        finished_at="2026-10-31T00:00:10+00:00",
    )

    markdown = result.to_markdown()
    for heading in ("### 已支持", "### 有歧义", "### 已拒绝", "### 待审"):
        assert heading in markdown
    lowered = markdown.lower()
    for forbidden in ("概率", "percent", "probability"):
        assert forbidden not in lowered


async def test_draft_failure_returns_model_failed_without_output() -> None:
    model = FakeReviewModel(
        probe=findings_payload(make_finding()),
        errors={"draft": [ModelResponseError("草稿失败")]},
    )

    result = await ReviewEngine(model).run(TASK)

    assert result.status == "model_failed"
    assert result.draft == ""
    assert result.final_output == ""
    assert result.findings == []


async def test_review_result_and_finding_to_dict_have_required_fields() -> None:
    model = FakeReviewModel(probe=findings_payload(make_finding()))

    result = await ReviewEngine(model).run(TASK)
    data = result.to_dict()

    assert set(data) == {
        "task",
        "status",
        "model_calls",
        "started_at",
        "finished_at",
        "draft",
        "final_output",
        "findings",
    }
    assert set(data["findings"][0]) == {
        "id",
        "title",
        "mechanism",
        "trigger",
        "rule_quote",
        "rule_source",
        "check_kind",
        "check",
        "expected_signal",
        "status",
        "review_reason",
    }


async def test_final_failure_returns_review_failed_with_draft() -> None:
    model = FakeReviewModel(
        probe=findings_payload(make_finding()),
        errors={"final": [ModelResponseError("修正失败")]},
    )

    result = await ReviewEngine(model).run(TASK)

    assert result.status == "review_failed"
    assert result.final_output == result.draft == "草稿结果"
    assert result.findings[0].status == "supported"
    assert model.stage_counts["final"] == 1
    assert result.model_calls == 4
