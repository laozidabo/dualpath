"""第三批探索性对照：direct / self_1 / verified_default / verified_interaction。

冻结后运行；生成阶段不接触隐藏测试，评分与变异检查在生成结束后离线执行。
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dualpath.budgeted import BudgetedDeepSeek, Ledger
from dualpath.cli import load_env_file
from dualpath.sandbox import extract_code, grade, sandbox_support
from dualpath.verification import DIRECT, Case, Completion, VerifiedDualPath

from protocol import PROTOCOL

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FROZEN_FILES = ("protocol.py", "build_tasks.py", "tasks.json", "validate.py", "run.py", "analyze.py")
FEEDBACK = """[SELF_FEEDBACK]
检查候选 Python 实现是否满足任务的每一项明确要求。寻找具体边界情况、反例和实现错误；说明需修改之处。
不要为题目未要求的输入引入限制。如果实现已正确，明确说无需修改。"""
REWRITE = """[SELF_REWRITE]
根据任务、候选代码和审查意见，交付完整正确的 Python 实现。审查意见也可能有误，请逐项核对任务再决定是否采纳。
仅返回一个完整的 Python 代码块，保留已正确的行为。"""
ARM_ORDER = ("self_1", "verified_default", "verified_interaction")


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def tasks():
    result = json.loads((HERE / "tasks.json").read_text())
    assert len(result) == PROTOCOL["task_count"]
    assert [task["id"] for task in result] == [f"I{i:03}" for i in range(1, PROTOCOL["task_count"] + 1)]
    return result


def hashes():
    paths = [HERE / name for name in FROZEN_FILES] + sorted((ROOT / "src").rglob("*.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def assert_frozen():
    frozen = json.loads((HERE / "freeze.json").read_text())
    if frozen["files"] != hashes() or frozen["protocol"] != PROTOCOL:
        raise RuntimeError("代码、题库或协议在冻结后发生变化，拒绝付费运行")
    return frozen


class ScriptedModel:
    """零费用演练：返回参考实现与空反例清单，用于验证管线，不作为实验数据。"""

    def __init__(self, known_tasks) -> None:
        self.tasks = known_tasks

    def _task(self, user: str) -> dict:
        try:
            data = json.loads(user)
        except (TypeError, ValueError):
            data = {"task": user}
        text = data.get("task", "") if isinstance(data, dict) else ""
        for task in self.tasks:
            if task["prompt"] == text:
                return task
        raise RuntimeError("演练模型无法定位任务")

    async def complete(self, system: str, user: str):
        stage = system.splitlines()[0]
        if stage in {"[DIRECT_EXECUTE]", "[SELF_REWRITE]", "[VERIFY_DIRECT]"}:
            return Completion(self._task(user)["reference"], "stop")
        if stage == "[SELF_FEEDBACK]":
            return Completion("无需修改。", "stop")
        if stage == "[VERIFY_DISCOVER]":
            return Completion('{"cases": []}', "stop")
        raise RuntimeError("演练模型不支持阶段：" + stage)


def read_key() -> str:
    load_env_file(ROOT / ".env")
    load_env_file(Path(".env"))
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        key = getpass.getpass("DeepSeek API key (hidden, memory only): ")
    if not key.strip():
        raise RuntimeError("没有可用的 DEEPSEEK_API_KEY")
    return key.strip()


async def run_self(model, task, initial, row, persist) -> None:
    feedback = await model.complete(
        FEEDBACK, json.dumps({"task": task["prompt"], "candidate": initial}, ensure_ascii=False)
    )
    if feedback.finish_reason != "stop":
        raise RuntimeError("自检意见未完整结束")
    rewrite = await model.complete(
        REWRITE,
        json.dumps(
            {"task": task["prompt"], "candidate": initial, "feedback": feedback.text},
            ensure_ascii=False,
        ),
    )
    if rewrite.finish_reason != "stop":
        raise RuntimeError("自检重写未完整结束")
    row["self_1"] = {"feedback": feedback.text, "output": rewrite.text}
    persist()


async def run_verified(model, task, initial, row, persist, *, interaction_focus: bool) -> None:
    engine = VerifiedDualPath(
        model,
        max_cases=PROTOCOL["verified_max_cases"],
        max_model_calls=PROTOCOL["verified_max_calls_after_shared_initial"],
        discovery_retries=PROTOCOL["verified_format_retries"],
        interaction_focus=interaction_focus,
    )
    result = await engine.run(task["prompt"], task["entry_point"], initial=initial)
    key = "verified_interaction" if interaction_focus else "verified_default"
    row[key] = result.to_dict()
    persist()


def grade_output(code: str, task: dict) -> dict:
    return grade(extract_code(code), task["tests"], 10)


def audit_cases(task: dict, result: dict | None) -> tuple[list[dict], int]:
    """返回逐案例的参考实现一致性检查，以及被接受案例数量。"""
    if not result or not result.get("cases"):
        return [], 0
    accepted = {d["id"] for d in result.get("decisions", []) if d["verdict"] == "accept"}
    audit = []
    for raw in result["cases"]:
        if raw["id"] not in accepted:
            continue
        case = Case(**raw)
        audit.append(
            {
                "id": case.id,
                "reference_check": grade(task["reference"], case.tests(task["entry_point"]), 10),
            }
        )
    return audit, len(audit)


def mutant_checks(task: dict, result: dict | None) -> list[dict]:
    if not result or not result.get("cases"):
        return []
    accepted = {d["id"] for d in result.get("decisions", []) if d["verdict"] == "accept"}
    cases = [Case(**raw) for raw in result["cases"] if raw["id"] in accepted]
    checks = []
    for mutant in task["mutants"]:
        statuses = [grade(mutant["code"], case.tests(task["entry_point"]), 10) for case in cases]
        checks.append(
            {
                "mutant": mutant["name"],
                "accepted_cases": len(cases),
                "caught": any(item["status"] in {"fail", "timeout"} for item in statuses),
            }
        )
    return checks


def evaluate_task(task: dict, row: dict) -> dict:
    grades = {}
    if row.get("initial"):
        grades["direct"] = grade_output(row["initial"], task)
    else:
        grades["direct"] = {"status": "unavailable", "passed": False, "detail": "no initial draft"}
    self_row = row.get("self_1")
    grades["self_1"] = grade_output(self_row["output"], task) if self_row else {
        "status": "unavailable",
        "passed": False,
        "detail": "self_1 unavailable",
    }
    for arm in ("verified_default", "verified_interaction"):
        result = row.get(arm)
        if result and result.get("final_output"):
            grades[arm] = grade_output(result["final_output"], task)
        else:
            grades[arm] = {
                "status": "unavailable",
                "passed": False,
                "detail": (result or {}).get("status", "missing"),
            }
    audits = {}
    mutants = {}
    for arm in ("verified_default", "verified_interaction"):
        result = row.get(arm)
        audit, accepted = audit_cases(task, result)
        audits[arm] = {"accepted": accepted, "cases": audit}
        mutants[arm] = mutant_checks(task, result)
    return {"grades": grades, "case_audit": audits, "mutants": mutants}


async def run(key: str | None, output: Path, *, scripted: bool) -> None:
    frozen = assert_frozen()
    if not sandbox_support()["supported"]:
        raise RuntimeError("沙箱不可用，无法评分")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "protocol.json", frozen)
    all_tasks = tasks()
    rows = []
    stopped = None
    with Ledger(output / "ledger.json", PROTOCOL["global_incremental_peak_guard_cny"]) as ledger:
        for index, task in enumerate(all_tasks):
            row = {"id": task["id"], "initial": ""}
            path = output / "results" / (task["id"] + ".json")

            def persist():
                write(path, row)

            def model_for(arm: str):
                if scripted:
                    return ScriptedModel(all_tasks)
                return BudgetedDeepSeek(
                    key,
                    ledger,
                    group=f"{task['id']}/{arm}",
                    group_limit_cny=PROTOCOL["condition_incremental_guard_cny"],
                )

            try:
                draft = await model_for("direct").complete(DIRECT, task["prompt"])
                if draft.finish_reason != "stop":
                    raise RuntimeError("初稿未完整结束")
                row["initial"] = draft.text
                persist()

                order = ARM_ORDER[index % 3 :] + ARM_ORDER[: index % 3]
                operations = {
                    "self_1": lambda: run_self(model_for("self_1"), task, row["initial"], row, persist),
                    "verified_default": lambda: run_verified(
                        model_for("verified_default"), task, row["initial"], row, persist, interaction_focus=False
                    ),
                    "verified_interaction": lambda: run_verified(
                        model_for("verified_interaction"), task, row["initial"], row, persist, interaction_focus=True
                    ),
                }
                for arm in order:
                    try:
                        await operations[arm]()
                    except Exception as error:  # noqa: BLE001 - 记录后按协议停止
                        row.setdefault("errors", []).append({"arm": arm, "error": type(error).__name__})
                        persist()
                row.update(evaluate_task(task, row))
                persist()
                bad = row.get("errors") or any(
                    grade_row["status"] in {"timeout", "infrastructure_error"}
                    for grade_row in row["grades"].values()
                )
                relevant = [
                    call
                    for call in ledger.data["calls"]
                    if call["group"].startswith(task["id"] + "/")
                ]
                if any(call["status"] != "ok" for call in relevant) or any(
                    call.get("response", {}).get("choices", [{}])[0].get("finish_reason") != "stop"
                    for call in relevant
                    if call.get("status") == "ok"
                ):
                    bad = True
                if bad:
                    stopped = "本题出现接口异常、截断或评分基础设施错误"
            except Exception as error:  # noqa: BLE001
                row.setdefault("errors", []).append({"arm": "setup", "error": type(error).__name__})
                persist()
                stopped = "初始化或初稿失败：" + type(error).__name__
            rows.append(row)
            write(
                output / "run_status.json",
                {
                    "stopped_reason": stopped,
                    "completed_tasks": len(rows),
                    "planned_tasks": len(all_tasks),
                    "conservative_spend_cny": ledger.spent_units / 1_000_000,
                },
            )
            print(
                f"{task['id']} 完成；累计保守记账 {ledger.spent_units / 1_000_000:.6f} 元",
                flush=True,
            )
            if stopped:
                break
    write(
        output / "summary.json",
        {
            "protocol": frozen["protocol"],
            "scripted": scripted,
            "attempted_tasks": [row["id"] for row in rows],
            "planned_tasks": len(all_tasks),
            "stopped_reason": stopped,
            "records": rows,
        },
    )
    print(json.dumps({"output": str(output), "tasks": len(rows), "stopped": stopped}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="第三批规则交互反例发现实验")
    parser.add_argument("--output", required=True, help="新的输出目录；已存在会失败")
    parser.add_argument("--scripted", action="store_true", help="零费用演练：脚本化响应，不调用模型")
    args = parser.parse_args()
    key = None if args.scripted else read_key()
    asyncio.run(run(key, Path(args.output).expanduser(), scripted=args.scripted))


if __name__ == "__main__":
    main()
