"""显式补充批次：仅运行连接中断前尚未尝试的题目，不重抽缺失结果。"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import run as pilot

HERE = Path(__file__).resolve().parent
SELECTED = [f"V{i:03}" for i in range(3, 9)]
PRIOR_RUN = pilot.ROOT / "verify-runs" / "pilot-2"
OUTPUT = pilot.ROOT / "verify-runs" / "supplement-1"
FREEZE = HERE / "supplement_freeze.json"


def manifest():
    original = pilot.assert_frozen()
    prior = json.loads((PRIOR_RUN / "ledger.json").read_text())
    assert all(c["status"] != "pending" for c in prior["calls"])
    assert {c["group"].split("/")[0] for c in prior["calls"]} == {"V001", "V002"}
    cost = sum(c.get("charged_units", c["reserved_units"]) for c in prior["calls"]) / 1_000_000
    total_cap = 4.34970048 + .024428 + cost + pilot.PROTOCOL["global_incremental_peak_guard_cny"]
    assert total_cap < 10
    return {"original_freeze": original, "selected_tasks": SELECTED,
            "amendment": "V002 第二轮重写发生 URLError 后原批次按规则停止；保留失败、不重抽 V001/V002；仅继续未见输出的 V003..V008。题目、提示词、参数、交替顺序及本批停止规则均不变。补充结果单列。",
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "prior_ledger_sha256": hashlib.sha256((PRIOR_RUN / "ledger.json").read_bytes()).hexdigest(),
            "prior_run_conservative_cny": cost, "cumulative_cap_cny": total_cap}


async def run_selected(key):
    frozen = json.loads(FREEZE.read_text())
    assert frozen["manifest"] == manifest(), "补充批次冻结不一致"
    original_tasks, original_check = pilot.tasks, pilot.assert_frozen
    selected = [t for t in original_tasks() if t["id"] in SELECTED]
    assert [t["id"] for t in selected] == SELECTED
    # 复用完全相同的生成及评分函数；起点 V003 保持奇偶执行顺序。
    pilot.tasks = lambda: selected
    pilot.assert_frozen = lambda: frozen
    try:
        await pilot.run(key, OUTPUT)
    finally:
        pilot.tasks, pilot.assert_frozen = original_tasks, original_check


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["freeze", "run"])
    action = parser.parse_args().action
    if action == "freeze":
        assert not FREEZE.exists(), "禁止覆盖补充冻结"
        pilot.write(FREEZE, {"created_at": datetime.now(timezone.utc).isoformat(), "manifest": manifest()})
    else:
        key = os.getenv("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek 密钥（不回显、不保存）：")
        asyncio.run(run_selected(key))
