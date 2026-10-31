"""离线汇总第三批实验；不调用模型、不需要密钥。"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _load(path: Path):
    return json.loads(path.read_text())


def analyze(output: Path) -> dict:
    summary = _load(output / "summary.json")
    ledger = _load(output / "ledger.json")
    protocol = summary["protocol"]
    arms = tuple(protocol["arms"])
    records = summary["records"]
    calls = ledger["calls"]
    assert all(call["status"] != "pending" for call in calls), "存在未确认的调用，不能汇总"

    def available(record: dict, arm: str) -> bool:
        return record["grades"][arm]["status"] in {"pass", "fail", "timeout"}

    comparisons = {}
    for arm in arms[1:]:
        pairs = [record for record in records if available(record, "direct") and available(record, arm)]
        comparisons[arm] = {
            "paired_n": len(pairs),
            "repairs": sum(not record["grades"]["direct"]["passed"] and record["grades"][arm]["passed"] for record in pairs),
            "regressions": sum(record["grades"]["direct"]["passed"] and not record["grades"][arm]["passed"] for record in pairs),
        }

    case_audit = {}
    mutants = {}
    for arm in ("verified_default", "verified_interaction"):
        generated = sum(len((record.get(arm) or {}).get("cases", [])) for record in records)
        reviewed = sum(
            len((record.get(arm) or {}).get("decisions", [])) for record in records
        )
        accepted = sum(record["case_audit"][arm]["accepted"] for record in records)
        agrees = sum(
            1
            for record in records
            for case in record["case_audit"][arm]["cases"]
            if case["reference_check"]["passed"]
        )
        checks = [item for record in records for item in record["mutants"][arm]]
        case_audit[arm] = {
            "generated": generated,
            "reviewed": reviewed,
            "accepted": accepted,
            "reference_agrees": agrees,
            "reference_disagrees": accepted - agrees,
            "false_accepts_vs_reference": accepted - agrees,
        }
        mutants[arm] = {
            "caught": sum(item["caught"] for item in checks),
            "total": len(checks),
            "caught_per_planned_task": round(sum(item["caught"] for item in checks) / len(records), 4),
            "tasks_with_accepted_cases": len(
                {record["id"] for record in records if record["case_audit"][arm]["accepted"]}
            ),
            "planned_tasks": len(records),
            "checks": checks,
        }

    costs = {}
    for arm in arms:
        selected = [call for call in calls if call["group"].endswith("/" + arm)]
        costs[arm] = {
            "calls": len(selected),
            "conservative_cny_including_unknown_reserves": sum(
                call.get("charged_units", call["reserved_units"]) for call in selected
            )
            / 1_000_000,
            "truncated_calls": sum(
                call.get("response", {}).get("choices", [{}])[0].get("finish_reason") == "length"
                for call in selected
            ),
            "error_calls": sum(call["status"] != "ok" for call in selected),
        }

    new_total = sum(
        call.get("charged_units", call["reserved_units"]) for call in calls
    ) / 1_000_000
    statuses = {
        arm: dict(Counter((record.get(arm) or {}).get("status", "missing") for record in records))
        for arm in ("verified_default", "verified_interaction")
    }
    result = {
        "batch": protocol["batch"],
        "scripted": summary.get("scripted", False),
        "interpretation": "探索性小样本；自建题、单模型、单次采样；不用于显著性结论",
        "attempted_tasks": len(records),
        "planned_tasks": summary["planned_tasks"],
        "stopped_reason": summary.get("stopped_reason"),
        "available": {arm: sum(available(record, arm) for record in records) for arm in arms},
        "passed": {arm: sum(record["grades"][arm]["passed"] for record in records) for arm in arms},
        "complete_pair_passed": {
            arm: sum(
                record["grades"][arm]["passed"]
                for record in records
                if all(available(record, other) for other in arms)
            )
            for arm in arms
        },
        "comparisons_vs_direct": comparisons,
        "verified_statuses": statuses,
        "case_audit": case_audit,
        "preselected_mutants": mutants,
        "costs": costs,
        "notes": {
            "mutant_denominator": "caught/total 的分母是有被接受案例的(题,臂)数，不同臂可能不同；caught_per_planned_task 以计划题数为分母，便于横向比较",
            "interaction_risk": "interaction_focus 只改发现提示词，不增加案例数；3 个案例预算下可能挤掉简单边界案例",
        },
        "new_experiment_conservative_cny": new_total,
        "all_experiments_conservative_cny": round(
            protocol["prior_cumulative_conservative_cny"] + new_total, 8
        ),
        "records": records,
    }
    (output / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    return result


def markdown(result: dict) -> str:
    arms = ("direct", "self_1", "verified_default", "verified_interaction")
    lines = [
        "# 第三批实验：默认发现 vs 规则交互发现",
        "",
        f"- 批次：`{result['batch']}`；脚本化演练：`{result['scripted']}`",
        f"- 计划题数：{result['planned_tasks']}；已尝试：{result['attempted_tasks']}",
        f"- 提前停止：{result['stopped_reason'] or '无'}",
        "",
        "## 隐藏测试通过",
        "",
        "| 方法 | 可用 | 通过 |",
        "| --- | ---: | ---: |",
    ]
    for arm in arms:
        lines.append(f"| `{arm}` | {result['available'][arm]} | {result['passed'][arm]} |")
    lines.extend(
        [
            "",
            "## 相对 direct 的配对比较",
            "",
            "| 方法 | 配对数 | 修复 | 退步 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for arm in arms[1:]:
        row = result["comparisons_vs_direct"][arm]
        lines.append(f"| `{arm}` | {row['paired_n']} | {row['repairs']} | {row['regressions']} |")
    lines.extend(
        [
            "",
            "## 反例与变异检出",
            "",
            "| 方法 | 生成 | 接受 | 参考实现同意 | 参考实现不同意 | 变异检出 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for arm in ("verified_default", "verified_interaction"):
        audit = result["case_audit"][arm]
        mutant = result["preselected_mutants"][arm]
        lines.append(
            f"| `{arm}` | {audit['generated']} | {audit['accepted']} | "
            f"{audit['reference_agrees']} | {audit['reference_disagrees']} | "
            f"{mutant['caught']}/{mutant['total']} |"
        )
    lines.extend(
        [
            "",
            "## 费用（峰值价保守账本）",
            "",
            "| 方法 | 调用 | 保守记账（元） | 截断 | 错误 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for arm in arms:
        cost = result["costs"][arm]
        lines.append(
            f"| `{arm}` | {cost['calls']} | {cost['conservative_cny_including_unknown_reserves']:.6f} | "
            f"{cost['truncated_calls']} | {cost['error_calls']} |"
        )
    lines.extend(
        [
            "",
            f"本批保守记账 **{result['new_experiment_conservative_cny']:.8f} 元**；"
            f"含此前实验累计 **{result['all_experiments_conservative_cny']:.8f} 元**。"
            "这是本地估算，不是供应商账单。",
            "",
            "> 探索性小样本：自建题、单模型、单次采样；不用于显著性、泛化或优越性结论。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总第三批实验")
    parser.add_argument("--output", required=True, help="run.py 产生的结果目录")
    args = parser.parse_args()
    output = Path(args.output).expanduser()
    result = analyze(output)
    (output / "analysis.md").write_text(markdown(result), encoding="utf-8")
    printable = {key: value for key, value in result.items() if key != "records"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
