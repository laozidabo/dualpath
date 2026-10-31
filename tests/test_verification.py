"""验证真正会改变发布决策的边界，包含真实隔离执行。"""
import asyncio
import json
from unittest.mock import patch

import pytest

from dualpath.sandbox import sandbox_support
from dualpath.verification import (
    Completion,
    ProtocolError,
    VerifiedDualPath,
    literal,
    parse_cases,
    parse_decisions,
    strict_json,
)

TASK = "实现 abs_value(x)，x 是整数。返回整数的绝对值。"
INITIAL = "def abs_value(x): return x"
REPAIRED = "def abs_value(x): return abs(x)"


def test_json_exponent_overflow_rejected():
    with pytest.raises(ProtocolError):
        strict_json('{"v":1e999}')


def case(id="C1", arg=-2, expected=2):
    return {
        "id": id,
        "rule": "返回整数的绝对值",
        "reason": "负数边界",
        "args": repr((arg,)),
        "kwargs": "{}",
        "expect": {"kind": "return", "value": repr(expected)},
    }


def discovery(rows=None):
    return json.dumps({"cases": rows if rows is not None else [case()]}, ensure_ascii=False)


def review(ids=("C1",), verdict="accept"):
    return json.dumps({"decisions": [{"id": id, "verdict": verdict, "reason": "按绝对值规则推导"} for id in ids]})


class Fake:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.requests = []

    async def complete(self, system, user):
        stage = system.splitlines()[0]
        self.requests.append((stage, user))
        default = {
            "[VERIFY_DIRECT]": INITIAL,
            "[VERIFY_DISCOVER]": discovery(),
            "[VERIFY_REVIEW]": review(),
            "[VERIFY_REPAIR]": REPAIRED,
        }
        response = self.responses.get(stage, default[stage])
        if isinstance(response, list):
            response = response.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize("text", [
    '[{"cases": []},', 'prefix {"cases": []}', '{"cases": []} trailing',
    '{"cases": [], "cases": []}', '{"v": NaN}', '```json\n{"cases": []}',
])
def test_reject_partial_or_ambiguous_json(text):
    with pytest.raises(ProtocolError):
        strict_json(text)


def test_complete_json_fence():
    assert strict_json('```json\n{"cases": []}\n```') == {"cases": []}


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').getcwd()",
        "[0] * 100000000",
        "set()",
        "1e999",
        "b'bytes'",
        "{" + "'x':" * 200 + "1}",
    ],
)
def test_literal_cannot_execute_or_expand(text):
    with pytest.raises(ProtocolError):
        literal(text)


def test_literal_preserves_tuples_dicts_and_sets():
    assert literal("([('a', 0, 4, ['r'])], {'r': [(2,2)]}, {1,2})") == ([('a', 0, 4, ['r'])], {'r': [(2, 2)]}, {1, 2})


@pytest.mark.parametrize("change", [
    {"rule": "题目没有说过的规则"}, {"args": "[1]"}, {"kwargs": "{1: 2}"},
    {"expect": {"kind": "raises", "type": "BaseException"}}, {"id": "__import__"},
])
def test_reject_invalid_case(change):
    with pytest.raises(ProtocolError):
        parse_cases(discovery([{**case(), **change}]), TASK, 3)


def test_duplicate_cases_and_missing_review_are_rejected():
    with pytest.raises(ProtocolError):
        parse_cases(discovery([case(), case()]), TASK, 3)
    with pytest.raises(ProtocolError):
        parse_decisions('{"decisions":[]}', parse_cases(discovery(), TASK, 3))


@pytest.mark.asyncio
async def test_missing_sandbox_does_not_spend_or_execute():
    fake = Fake()
    with patch("dualpath.verification.sandbox_support", return_value={"supported": False}):
        result = await VerifiedDualPath(fake).run(TASK, "abs_value", initial=INITIAL)
    assert result.status == "sandbox_unavailable"
    assert result.final_output == INITIAL
    assert not fake.requests


@pytest.mark.asyncio
async def test_cancellation_drains_both_branches():
    started = asyncio.Event()
    active = 0

    class Blocking:
        async def complete(self, system, user):
            nonlocal active
            active += 1
            if active == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

    with patch("dualpath.verification.sandbox_support", return_value={"supported": True}):
        job = asyncio.create_task(VerifiedDualPath(Blocking()).run(TASK, "abs_value"))
        await asyncio.wait_for(started.wait(), 2)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    assert active == 0


requires_sandbox = pytest.mark.skipif(not sandbox_support()["supported"], reason="需要 bwrap")


@requires_sandbox
@pytest.mark.asyncio
async def test_repairs_only_after_observed_failure_and_preserves_independence():
    fake = Fake()
    result = await VerifiedDualPath(fake).run(TASK, "abs_value")
    assert result.status == "repaired"
    assert result.final_output == REPAIRED
    assert result.initial_checks[0]["status"] == "fail"
    assert result.repaired_checks[0]["status"] == "pass"
    assert result.model_calls == 4
    for stage, user in fake.requests:
        if stage in {"[VERIFY_DISCOVER]", "[VERIFY_REVIEW]"}:
            assert INITIAL not in user
            assert "candidate" not in json.loads(user)
    assert result.expectation_assurance == "model_reviewed_not_proven"


@requires_sandbox
@pytest.mark.asyncio
async def test_repair_regression_keeps_initial():
    fake = Fake({"[VERIFY_DISCOVER]": discovery([case(), case("C2", 5, 5)]),
                 "[VERIFY_REVIEW]": review(("C1", "C2")),
                 "[VERIFY_REPAIR]": "def abs_value(x): return -x"})
    result = await VerifiedDualPath(fake).run(TASK, "abs_value")
    assert result.status == "repair_rejected"
    assert result.final_output == INITIAL
    assert [r["status"] for r in result.initial_checks] == ["fail", "pass"]
    assert [r["status"] for r in result.repaired_checks] == ["pass", "fail"]


@requires_sandbox
@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["ambiguous", "reject"])
async def test_unreliable_expectation_does_not_trigger_repair(verdict):
    fake = Fake({"[VERIFY_REVIEW]": review(verdict=verdict)})
    result = await VerifiedDualPath(fake).run(TASK, "abs_value")
    assert result.status == "no_accepted_cases"
    assert not result.initial_checks and not result.attempted_repair
    assert result.final_output == INITIAL


@requires_sandbox
@pytest.mark.asyncio
async def test_passing_initial_stops_before_rewrite():
    result = await VerifiedDualPath(Fake()).run(TASK, "abs_value", initial=REPAIRED)
    assert result.status == "checks_passed"
    assert result.model_calls == 2
    assert result.final_output == REPAIRED


@requires_sandbox
@pytest.mark.asyncio
async def test_truncation_retries_once_and_never_salvages_inner_object():
    incomplete = Completion('[{"cases": []},', "length")
    fake = Fake({"[VERIFY_DISCOVER]": [incomplete, discovery()]})
    result = await VerifiedDualPath(fake).run(TASK, "abs_value")
    assert result.status == "repaired"
    assert result.model_calls == 5
    fake = Fake({"[VERIFY_DISCOVER]": [incomplete, incomplete]})
    result = await VerifiedDualPath(fake).run(TASK, "abs_value")
    assert result.status == "discovery_unavailable"
    assert result.final_output == INITIAL and result.model_calls == 3


@requires_sandbox
@pytest.mark.asyncio
async def test_call_limit_prevents_unbudgeted_repair():
    result = await VerifiedDualPath(Fake(), max_model_calls=3).run(TASK, "abs_value")
    assert result.status == "repair_unavailable"
    assert result.model_calls == 3 and result.final_output == INITIAL


@requires_sandbox
@pytest.mark.asyncio
async def test_infrastructure_failure_is_not_a_counterexample():
    with patch("dualpath.verification.grade", return_value={"status": "infrastructure_error", "passed": False}):
        result = await VerifiedDualPath(Fake()).run(TASK, "abs_value")
    assert result.status == "verification_unavailable"
    assert result.model_calls == 3 and result.final_output == INITIAL


@requires_sandbox
@pytest.mark.asyncio
async def test_exception_does_not_leak_sensitive_details():
    fake = Fake({"[VERIFY_REVIEW]": RuntimeError("private credential detail")})
    result = await VerifiedDualPath(fake).run(TASK, "abs_value")
    assert result.status == "review_unavailable"
    assert "private credential detail" not in json.dumps(result.to_dict())


@requires_sandbox
@pytest.mark.asyncio
async def test_truncated_repair_preserves_initial():
    result = await VerifiedDualPath(Fake({"[VERIFY_REPAIR]": Completion(REPAIRED, "length")})).run(TASK, "abs_value")
    assert result.status == "repair_unavailable"
    assert result.final_output == INITIAL


@requires_sandbox
@pytest.mark.asyncio
async def test_interaction_focus_changes_only_discovery_prompt():
    class PromptFake(Fake):
        def __init__(self):
            super().__init__()
            self.systems = []

        async def complete(self, system, user):
            self.systems.append(system)
            return await super().complete(system, user)

    fake = PromptFake()
    result = await VerifiedDualPath(fake, interaction_focus=True).run(TASK, "abs_value", initial=REPAIRED)
    assert result.status == "checks_passed"
    assert result.model_calls == 2
    assert result.events[0] == {"discovery_strategy": "interaction_focus"}
    assert any("额外覆盖要求" in system for system in fake.systems)
    assert all("额外覆盖要求" not in system for system in fake.systems if not system.startswith("[VERIFY_DISCOVER]"))

    default_fake = PromptFake()
    await VerifiedDualPath(default_fake).run(TASK, "abs_value", initial=REPAIRED)
    assert all("额外覆盖要求" not in system for system in default_fake.systems)
