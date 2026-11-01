"""从冻结快照重建第三批全部请求、执行、评分和账本；禁止联网、不修改原始记录。"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_SHA256 = "d0b390863d1a7c248c79c30ce607247208fe497ce9296b4c3e63dde3f04c1ae0"
FREEZE_PATH = "experiments/interaction_pilot/freeze.json"


def load(path):
    return json.loads(Path(path).read_text())


def stable(value):
    """执行耗时不要求复现；其余字段逐项比较。"""
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items() if key != "duration_seconds"}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def restore(archive, target):
    data = archive.read_bytes()
    require(hashlib.sha256(data).hexdigest() == ARCHIVE_SHA256, "冻结源码归档散列不一致")
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        frozen = json.loads(bundle.read(FREEZE_PATH))
        names = [*frozen["files"], FREEZE_PATH]
        require(sorted(bundle.namelist()) == sorted(names), "冻结归档文件集合不一致")
        for name in names:
            relative = Path(name)
            require(not relative.is_absolute() and ".." not in relative.parts, "归档路径无效")
            content = bundle.read(name)
            if name in frozen["files"]:
                require(hashlib.sha256(content).hexdigest() == frozen["files"][name], "冻结文件散列不一致")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    return frozen


class ReplayTape:
    """按原始顺序精确匹配组别与完整请求；不允许额外调用或未消费响应。"""

    def __init__(self, calls):
        self.calls = calls
        self.position = 0
        self.mismatches = []
        require(all(call["status"] == "ok" for call in calls), "本复放器仅支持第三批完整响应")

    def reply(self, group, payload):
        if self.position >= len(self.calls):
            self.mismatches.append("存在额外请求")
            raise RuntimeError(self.mismatches[-1])
        recorded = self.calls[self.position]
        if recorded["group"] != group or recorded["request"] != payload:
            self.mismatches.append(f"第 {self.position + 1} 个请求或组别不一致")
            raise RuntimeError(self.mismatches[-1])
        self.position += 1
        return copy.deepcopy(recorded["response"])

    def finish(self):
        require(not self.mismatches, "复放请求存在差异")
        require(self.position == len(self.calls), "原始响应没有全部消费")


def worker(source, original, output):
    network_attempts = []

    def deny_network(*args, **kwargs):
        network_attempts.append(True)
        raise RuntimeError("离线复放禁止联网")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    sys.path[:0] = [str(source / "experiments/interaction_pilot"), str(source / "src")]
    import analyze as frozen_analysis
    import run as frozen_run

    from dualpath.budgeted import BudgetedDeepSeek
    from dualpath.sandbox import grade

    frozen = frozen_run.assert_frozen()
    require(load(original / "protocol.json") == frozen, "运行记录协议与冻结源码不一致")
    require(not load(original / "summary.json")["scripted"], "不可把脚本演练作为真实实验复放")
    original_ledger = load(original / "ledger.json")
    tape = ReplayTape(original_ledger["calls"])

    class OfflineModel(BudgetedDeepSeek):
        def __init__(self, api_key, ledger, **kwargs):
            group = kwargs["group"]
            super().__init__("offline-replay", ledger, transport=lambda payload: tape.reply(group, payload), **kwargs)

    frozen_run.BudgetedDeepSeek = OfflineModel
    with redirect_stdout(io.StringIO()):
        asyncio.run(frozen_run.run(None, output, scripted=False))
    tape.finish()
    require(not network_attempts, "复放曾尝试网络访问")

    result_paths = sorted((original / "results").glob("*.json"))
    require([p.name for p in result_paths] == sorted(p.name for p in (output / "results").glob("*.json")),
            "逐题结果集合不一致")
    for path in result_paths:
        require(stable(load(path)) == stable(load(output / "results" / path.name)), f"逐题结果不一致：{path.name}")
    for filename in ("summary.json", "run_status.json"):
        require(stable(load(original / filename)) == stable(load(output / filename)), f"{filename} 不一致")

    replay_ledger = load(output / "ledger.json")
    require({k: v for k, v in original_ledger.items() if k != "calls"}
            == {k: v for k, v in replay_ledger.items() if k != "calls"}, "账本元数据不一致")
    def accounting(call):
        return {key: value for key, value in call.items() if key not in {"started_at", "elapsed_seconds"}}
    require([accounting(call) for call in original_ledger["calls"]]
            == [accounting(call) for call in replay_ledger["calls"]], "费用、响应或请求记录不一致")

    analysis = frozen_analysis.analyze(output)
    require(stable(analysis) == stable(load(original / "analysis.json")), "汇总分析不一致")
    require(frozen_analysis.markdown(analysis) == (original / "analysis.md").read_text(), "Markdown 汇总不一致")
    validation = []
    for task in frozen_run.tasks():
        reference = grade(task["reference"], task["tests"], 10)
        mutants = [grade(mutant["code"], task["tests"], 10) for mutant in task["mutants"]]
        require(reference["passed"] and all(item["status"] in {"fail", "timeout"} for item in mutants),
                f"题库校验失败：{task['id']}")
        validation.append({"id": task["id"], "reference_passed": True, "mutants_rejected": len(mutants)})
    return {
        "batch": "interaction_pilot", "network_calls": 0, "all_checks_passed": True,
        "frozen_source_sha256": ARCHIVE_SHA256, "matched_requests": tape.position,
        "matched_task_results": len(result_paths), "matched_accounting": True,
        "matched_analysis": True, "reference_tasks_passed": len(validation),
        "hidden_test_mutants_rejected": sum(row["mutants_rejected"] for row in validation),
        "mutants_caught": {arm: {key: analysis["preselected_mutants"][arm][key] for key in ("caught", "total")}
                            for arm in ("verified_default", "verified_interaction")},
        "conservative_cny": analysis["new_experiment_conservative_cny"],
        "validation": validation,
        "limits": "仅复现历史请求、响应驱动的流程和评分，不代表新模型采样或修复收益。",
    }


def main():
    parser = argparse.ArgumentParser(description="第三批冻结实验的零费用全流程复放")
    parser.add_argument("--records", type=Path, default=ROOT / "verify-runs/interaction-pilot")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--worker-source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_source:
        result = worker(args.worker_source, args.records, args.worker_output)
    else:
        with tempfile.TemporaryDirectory(prefix="dualpath-interaction-replay-") as directory:
            target = Path(directory)
            restore(ROOT / "artifacts/interaction-pilot-frozen-source.zip", target)
            environment = dict(os.environ, PYTHONPATH=str(target / "src"))
            for key in list(environment):
                if key.endswith("API_KEY"):
                    environment.pop(key)
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker-source", str(target),
                 "--worker-output", str(target / "replayed"), "--records", str(args.records.resolve())],
                cwd=target, env=environment, text=True, capture_output=True,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr)
            result = json.loads(completed.stdout)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        # 只创建新报告，避免覆盖原始记录或上一次审计。
        with args.report.open("x") as stream:
            stream.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
