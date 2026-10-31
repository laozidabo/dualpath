"""新版本探索性对照；冻结后才调用接口，隐藏测试仅在生成结束后运行。"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path

from dualpath.budgeted import BudgetedDeepSeek, Ledger
from dualpath.sandbox import extract_code, grade, sandbox_support
from dualpath.verification import Case, DIRECT, VerifiedDualPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FEEDBACK = """[SELF_FEEDBACK]
检查候选 Python 实现是否满足任务的每一项明确要求。寻找具体边界情况、反例和实现错误；说明需修改之处。
不要为题目未要求的输入引入限制。如果实现已正确，明确说无需修改。"""
REWRITE = """[SELF_REWRITE]
根据任务、候选代码和审查意见，交付完整正确的 Python 实现。审查意见也可能有误，请逐项核对任务再决定是否采纳。
仅返回一个完整的 Python 代码块，保留已正确的行为。"""
PROTOCOL = {
    "purpose": "8 题探索性流程试验，不用于宣称优越性",
    "model": "deepseek-flash", "temperature": .2, "thinking": "disabled",
    "max_tokens": 2048, "repetitions": 1, "global_incremental_peak_guard_cny": 4.975572,
    "prior_auth_failure_reserved_cny": .024428,
    "amendment": "首个初稿请求认证失败且无模型输出；保留旧冻结和账本，补充 HTTP 状态日志及可用样本数；不更改题目或提示词",
    "old_experiment_peak_ledger_cny": 4.34970048,
    "conditions": ["direct", "self_1", "self_3", "verified"],
    "shared_initial": True, "self_1_is_checkpoint_of_self_3": True,
    "self_rounds": 3, "condition_incremental_guard_cny": .30,
    "verified_max_cases": 3, "verified_max_calls_after_shared_initial": 6,
    "verified_format_retries": 1, "verified_repair_attempts": 1,
    "ordering": "V001/V003/V005/V007 先 self；其他先 verified",
    "hidden_tests_available_to_generation": False,
    "exact_cost_matched": False, "no_network_retries": True,
    "stop_rule": "任一接口错误或截断使本题保留已有输出，完成本题后停止；不重抽题、不替换题",
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def tasks():
    result = json.loads((HERE / "tasks.json").read_text())
    assert len(result) == 8 and [t["id"] for t in result] == [f"V{i:03}" for i in range(1,9)]
    return result


def hashes():
    paths = list((ROOT / "src").rglob("*.py")) + [HERE / name for name in ("run.py", "build_tasks.py", "tasks.json")]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def validate_and_freeze():
    if (HERE / "freeze.json").exists():
        raise RuntimeError("冻结文件已存在，禁止覆盖")
    records = []
    for task in tasks():
        reference = grade(task["reference"], task["tests"], 6)
        mutants = [grade(m["code"], task["tests"], 6) for m in task["mutants"]]
        valid = reference["passed"] and all(m["status"] in {"fail", "timeout"} for m in mutants)
        records.append({"id": task["id"], "reference": reference, "mutants": mutants, "valid": valid})
        print(task["id"], "有效" if valid else "需要修正", flush=True)
    write(HERE / "validation.json", records)
    if not all(r["valid"] for r in records):
        raise RuntimeError("参考实现或变异检查未通过，不能开始实验")
    write(HERE / "freeze.json", {"created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL, "files": hashes()})


def assert_frozen():
    frozen = json.loads((HERE / "freeze.json").read_text())
    if frozen["files"] != hashes() or frozen["protocol"] != PROTOCOL:
        raise RuntimeError("代码、题目或协议在冻结后发生变化，拒绝付费运行")
    return frozen


async def run(key, output):
    frozen = assert_frozen()
    if not sandbox_support()["supported"]:
        raise RuntimeError("沙箱不可用")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "protocol.json", frozen)
    stopped = None
    with Ledger(output / "ledger.json", PROTOCOL["global_incremental_peak_guard_cny"]) as ledger:
        for index, task in enumerate(tasks()):
            id, prompt = task["id"], task["prompt"]
            path = output / "results" / (id + ".json")
            row = {"id": id, "initial": "", "self_rounds": [], "verified": None}
            def persist():
                write(path, row)
            try:
                direct_model = BudgetedDeepSeek(key, ledger, group=id+"/direct", group_limit_cny=.30)
                raw = await direct_model.complete(DIRECT, prompt)
                if raw.finish_reason != "stop":
                    raise RuntimeError("初稿未完整结束")
                row["initial"] = raw.text
                persist()

                async def self_refine():
                    model = BudgetedDeepSeek(key, ledger, group=id+"/self", group_limit_cny=.30)
                    candidate = row["initial"]
                    for round_number in range(1,4):
                        feedback = await model.complete(FEEDBACK, json.dumps({"task": prompt, "candidate": candidate}, ensure_ascii=False))
                        if feedback.finish_reason != "stop":
                            raise RuntimeError("自检意见未完整结束")
                        rewrite = await model.complete(REWRITE, json.dumps({"task": prompt, "candidate": candidate, "feedback": feedback.text}, ensure_ascii=False))
                        if rewrite.finish_reason != "stop":
                            raise RuntimeError("自检修正未完整结束")
                        candidate = rewrite.text
                        row["self_rounds"].append({"round": round_number, "feedback": feedback.text, "output": candidate})
                        persist()

                async def verify():
                    model = BudgetedDeepSeek(key, ledger, group=id+"/verified", group_limit_cny=.30)
                    result = await VerifiedDualPath(model).run(prompt, task["entry_point"], initial=row["initial"])
                    row["verified"] = result.to_dict()
                    persist()

                for operation in ((self_refine, verify) if index % 2 == 0 else (verify, self_refine)):
                    try:
                        await operation()
                    except Exception as error:
                        row.setdefault("errors", []).append(type(error).__name__)
                        persist()
                relevant = [c for c in ledger.data["calls"] if c["group"].startswith(id+"/")]
                if row.get("errors") or any(c["status"] != "ok" or c.get("response",{}).get("choices",[{}])[0].get("finish_reason") != "stop" for c in relevant):
                    stopped = "本题出现接口异常或截断"
            except Exception as error:
                stopped = type(error).__name__
                row.setdefault("errors", []).append(type(error).__name__)
            persist()
            print(f"{id} 已保存；保守累计 {ledger.spent_units/1_000_000:.6f} 元", flush=True)
            if stopped:
                break
        write(output / "run_status.json", {"stopped_reason": stopped, "conservative_incremental_cny": ledger.spent_units/1_000_000})
    score(output)


def score(output):
    """重新执行所有最终输出和已生成反例；不调用任何模型。"""
    records = []
    for task in tasks():
        path = output / "results" / (task["id"]+".json")
        if not path.exists():
            continue
        row = json.loads(path.read_text())
        outputs = {"direct": row["initial"]}
        for count in (1,3):
            outputs[f"self_{count}"] = row["self_rounds"][count-1]["output"] if len(row["self_rounds"]) >= count else None
        outputs["verified"] = row["verified"]["final_output"] if row["verified"] else None
        grades = {name: grade(extract_code(text), task["tests"], 6) if text else {"status": "unavailable", "passed": False} for name,text in outputs.items()}
        audits = []
        if row["verified"]:
            decisions = {d["id"]: d["verdict"] for d in row["verified"]["decisions"]}
            for c in row["verified"]["cases"]:
                outcome = grade(task["reference"], Case(**c).tests(task["entry_point"]), 6)
                audits.append({"id": c["id"], "decision": decisions.get(c["id"]), "reference_check": outcome})
        records.append({"id": task["id"], "grades": grades, "case_audit": audits,
                        "verified_status": row["verified"]["status"] if row["verified"] else "unavailable"})
    ledger = json.loads((output / "ledger.json").read_text())
    calls = ledger["calls"]
    costs = {}
    for arm in ("direct","self_1","self_3","verified"):
        selected = []
        for task in tasks():
            group = task["id"] + "/" + ("self" if arm.startswith("self") else arm)
            entries = [c for c in calls if c["group"] == group]
            selected.extend(entries[:2] if arm == "self_1" else entries)
        costs[arm] = {"calls": len(selected), "incremental_peak_cny": sum(c.get("charged_units", c["reserved_units"]) for c in selected)/1_000_000,
                      "tokens": sum(sum(c.get("response",{}).get("usage",{}).get(k,0) for k in ("prompt_tokens","completion_tokens")) for c in selected)}
    summary = {"attempted_tasks": len(records),
               "completed_paired_tasks": sum(all(g["status"] in {"pass","fail","timeout"} for g in r["grades"].values()) for r in records),
               "available": {arm: sum(r["grades"][arm]["status"] in {"pass","fail","timeout"} for r in records) for arm in costs},
               "passed": {arm: sum(r["grades"][arm]["passed"] for r in records) for arm in costs},
               "verified_statuses": dict(Counter(r["verified_status"] for r in records)),
               "incremental_costs": costs, "records": records,
               "conservative_new_spend_cny": sum(c.get("charged_units", c["reserved_units"]) for c in calls)/1_000_000,
               "price_note": "高峰价保守记账；self_1 是 self_3 的中间结果，费用不可重复相加"}
    write(output / "summary.json", summary)
    print(json.dumps({k:v for k,v in summary.items() if k != "records"}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["freeze","run","score"])
    parser.add_argument("--output", type=Path, default=ROOT / "verify-runs" / "pilot-2")
    args = parser.parse_args()
    if args.action == "freeze":
        validate_and_freeze()
    elif args.action == "score":
        score(args.output)
    else:
        key = os.getenv("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek 密钥（不回显、不保存）：")
        asyncio.run(run(key, args.output))
