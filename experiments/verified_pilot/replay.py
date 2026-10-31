"""只用已保存响应复放流程、核对请求和隐藏评分；不读取密钥或调用网络。"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from run import DIRECT, FEEDBACK, REWRITE, ROOT, assert_frozen, tasks, write
from dualpath.budgeted import cost_units
from dualpath.sandbox import extract_code, grade
from dualpath.verification import Case, Completion, VerifiedDualPath


def stable(value):
    """仅剔除隔离执行的墙钟耗时，其余结果和观察仍须一致。"""
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k != "duration_seconds"}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


class Replay:
    def __init__(self, calls):
        self.calls = list(calls)
        self.position = 0

    async def complete(self, system, user):
        assert self.position < len(self.calls), "复放发生额外调用"
        row = self.calls[self.position]
        self.position += 1
        messages = row["request"]["messages"]
        assert messages[0] == {"role": "system", "content": system}
        assert messages[1]["role"] == "user"
        if system.startswith("[VERIFY_REPAIR]"):
            assert stable(json.loads(messages[1]["content"])) == stable(json.loads(user))
        else:
            assert messages[1]["content"] == user, "请求与已保存来源不一致"
        if row["status"] != "ok":
            raise RuntimeError("复放已记录的接口失败")
        choice = row["response"]["choices"][0]
        return Completion(choice["message"]["content"], choice["finish_reason"])


async def audit(output):
    frozen = assert_frozen()
    protocol = json.loads((output / "protocol.json").read_text())
    prior_run_cost = 0
    if "manifest" in protocol:
        from supplement import FREEZE, manifest
        assert protocol == json.loads(FREEZE.read_text())
        assert protocol["manifest"] == manifest()
        prior_run_cost = protocol["manifest"]["prior_run_conservative_cny"]
    else:
        assert protocol == frozen
    ledger = json.loads((output / "ledger.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    calls = ledger["calls"]
    assert all(c["status"] != "pending" for c in calls), "运行尚未结束"
    for c in calls:
        assert "Authorization" not in json.dumps(c)
        if "charged_units" in c:
            assert cost_units(c["response"]["usage"]) == c["charged_units"]
            assert c["charged_units"] <= c["reserved_units"]
    cost = sum(c.get("charged_units", c["reserved_units"]) for c in calls)
    assert cost <= ledger["limit_units"]
    records = []
    for task in tasks():
        path = output / "results" / (task["id"] + ".json")
        if not path.exists():
            continue
        row = json.loads(path.read_text())
        get = lambda arm: [c for c in calls if c["group"] == task["id"] + "/" + arm]
        direct = Replay(get("direct"))
        if row["initial"]:
            initial = await direct.complete(DIRECT, task["prompt"])
            assert initial.finish_reason == "stop" and initial.text == row["initial"]
        self_model = Replay(get("self"))
        candidate = row["initial"]
        for step in row["self_rounds"]:
            feedback = await self_model.complete(FEEDBACK, json.dumps({"task": task["prompt"], "candidate": candidate}, ensure_ascii=False))
            assert feedback.text == step["feedback"] and feedback.finish_reason == "stop"
            rewrite = await self_model.complete(REWRITE, json.dumps({"task": task["prompt"], "candidate": candidate, "feedback": feedback.text}, ensure_ascii=False))
            assert rewrite.text == step["output"] and rewrite.finish_reason == "stop"
            candidate = rewrite.text
        if self_model.position < len(self_model.calls):
            # 中断轮的反馈或重写也必须能从同一候选重建。
            assert row.get("errors")
            try:
                feedback = await self_model.complete(FEEDBACK, json.dumps({"task": task["prompt"], "candidate": candidate}, ensure_ascii=False))
                if feedback.finish_reason == "stop":
                    rewrite = await self_model.complete(REWRITE, json.dumps({"task": task["prompt"], "candidate": candidate, "feedback": feedback.text}, ensure_ascii=False))
                    assert rewrite.finish_reason != "stop"
            except RuntimeError:
                assert self_model.calls[self_model.position-1]["status"] == "error"
        verified = Replay(get("verified"))
        if row["verified"]:
            result = await VerifiedDualPath(verified).run(task["prompt"], task["entry_point"], initial=row["initial"])
            assert stable(result.to_dict()) == stable(row["verified"]), "验证流程复放不一致"
            assert verified.position == len(verified.calls)
        assert direct.position == len(direct.calls)
        assert self_model.position == len(self_model.calls)
        saved = next(r for r in summary["records"] if r["id"] == task["id"])
        outputs = {"direct": row["initial"], "verified": row["verified"]["final_output"] if row["verified"] else None}
        for count in (1, 3):
            outputs[f"self_{count}"] = row["self_rounds"][count-1]["output"] if len(row["self_rounds"]) >= count else None
        grades = {name: grade(extract_code(text), task["tests"], 6) if text else {"status": "unavailable", "passed": False} for name, text in outputs.items()}
        assert stable(grades) == stable(saved["grades"]), "隐藏评分复放不一致"
        audits = []
        if row["verified"]:
            decisions = {d["id"]: d["verdict"] for d in row["verified"]["decisions"]}
            for case in row["verified"]["cases"]:
                audits.append({"id": case["id"], "decision": decisions.get(case["id"]), "reference_check": grade(task["reference"], Case(**case).tests(task["entry_point"]), 6)})
        assert stable(audits) == stable(saved["case_audit"])
        records.append({"id": task["id"], "requests_reconstructed": direct.position + self_model.position + verified.position,
                        "verified_replay_matches": bool(row["verified"]), "hidden_grades_match": True, "case_audit_matches": True})
    report = {"network_calls": 0, "frozen_files_verified": len(frozen["files"]),
              "ledger_call_statuses": dict(Counter(c["status"] for c in calls)),
              "conservative_run_cny": cost / 1_000_000,
              "cumulative_conservative_cny": 4.34970048 + .024428 + prior_run_cost + cost / 1_000_000,
              "records": records, "all_checks_passed": True}
    write(output / "replay_audit.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "verify-runs" / "pilot-2")
    asyncio.run(audit(parser.parse_args().output))
