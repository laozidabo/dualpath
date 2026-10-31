"""合并原批次与明确标记的补充批次；补做离线反例覆盖诊断，不调用模型。"""
from __future__ import annotations

import json
from collections import Counter

from run import ROOT, tasks, write
from dualpath.sandbox import grade
from dualpath.verification import Case


def analyze():
    arms = ("direct", "self_1", "self_3", "verified")
    batches = [ROOT / "verify-runs" / name for name in ("pilot-2", "supplement-1")]
    summaries = [json.loads((p / "summary.json").read_text()) for p in batches]
    records = [r for s in summaries for r in s["records"]]
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)), "禁止重复抽样后合并"
    complete = [r for r in records if all(r["grades"][a]["status"] in {"pass", "fail", "timeout"} for a in arms)]
    calls = [c for p in batches for c in json.loads((p / "ledger.json").read_text())["calls"]]
    assert all(c["status"] != "pending" for c in calls)
    comparisons = {}
    for arm in arms[1:]:
        pairs = [r for r in records if r["grades"]["direct"]["status"] in {"pass", "fail", "timeout"} and r["grades"][arm]["status"] in {"pass", "fail", "timeout"}]
        comparisons[arm] = {"paired_n": len(pairs),
                            "repairs": sum(not r["grades"]["direct"]["passed"] and r["grades"][arm]["passed"] for r in pairs),
                            "regressions": sum(r["grades"]["direct"]["passed"] and not r["grades"][arm]["passed"] for r in pairs)}
    counterexamples = Counter()
    for r in records:
        for case in r["case_audit"]:
            decision = case["decision"] or "unreviewed"
            counterexamples["generated"] += 1
            counterexamples[decision] += 1
            counterexamples["reference_agrees" if case["reference_check"]["passed"] else "reference_disagrees"] += 1
            if decision == "accept" and not case["reference_check"]["passed"]:
                counterexamples["false_accepts_vs_reference"] += 1
    mutants = []
    for task in tasks():
        matching = [p / "results" / (task["id"] + ".json") for p in batches if (p / "results" / (task["id"] + ".json")).exists()]
        if not matching:
            continue
        row = json.loads(matching[0].read_text())
        result = row["verified"]
        accepted = {d["id"] for d in result["decisions"] if d["verdict"] == "accept"} if result else set()
        cases = [Case(**c) for c in result["cases"] if c["id"] in accepted] if result else []
        for mutant in task["mutants"]:
            checks = [{"id": c.id, **grade(mutant["code"], c.tests(task["entry_point"]), 6)} for c in cases]
            mutants.append({"id": task["id"], "mutant": mutant["name"], "accepted_cases": len(cases), "caught": any(c["status"] in {"fail", "timeout"} for c in checks), "checks": checks})
    costs = {}
    for arm in arms:
        selected = []
        for id in ids:
            group = id + "/" + ("self" if arm.startswith("self") else arm)
            entries = [c for c in calls if c["group"] == group]
            selected.extend(entries[:2] if arm == "self_1" else entries)
        costs[arm] = {"calls": len(selected), "incremental_peak_cny_including_unknown_reserves": sum(c.get("charged_units", c["reserved_units"]) for c in selected) / 1e6}
    prior_failed = json.loads((ROOT / "verify-runs" / "pilot" / "ledger.json").read_text())["calls"]
    new_total = sum(c.get("charged_units", c["reserved_units"]) for c in calls + prior_failed) / 1e6
    result = {"interpretation": "探索性合并；保留 V002 中断缺失；补充批次不替换原批次", "api_calls_for_this_analysis": 0,
              "batches": [{"name": p.name, "attempted": s["attempted_tasks"], "completed_paired": s["completed_paired_tasks"]} for p, s in zip(batches, summaries)],
              "attempted_tasks": len(records), "complete_all_arm_pairs": len(complete),
              "available": {a: sum(r["grades"][a]["status"] in {"pass", "fail", "timeout"} for r in records) for a in arms},
              "passed": {a: sum(r["grades"][a]["passed"] for r in records) for a in arms},
              "complete_pair_passed": {a: sum(r["grades"][a]["passed"] for r in complete) for a in arms},
              "comparisons_vs_direct": comparisons,
              "verified_statuses": dict(Counter(r["verified_status"] for r in records)),
              "counterexample_reference_audit": dict(counterexamples),
              "preselected_mutants_caught": sum(m["caught"] for m in mutants), "preselected_mutants_total": len(mutants),
              "mutant_checks": mutants, "costs": costs,
              "new_experiment_conservative_cny_including_all_error_reserves": new_total,
              "all_experiments_conservative_cny": round(4.34970048 + new_total, 8),
              "unknown_error_reserves_cny": sum(c["reserved_units"] for c in calls + prior_failed if "charged_units" not in c) / 1e6,
              "successful_calls": sum(c["status"] == "ok" for c in calls),
              "failed_calls_including_prior_auth_failure": sum(c["status"] == "error" for c in calls + prior_failed),
              "truncated_calls": sum(c.get("response", {}).get("choices", [{}])[0].get("finish_reason") == "length" for c in calls),
              "records": records}
    write(ROOT / "verify-runs" / "combined_analysis.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in {"records", "mutant_checks"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    analyze()
