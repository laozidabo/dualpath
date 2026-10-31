"""离线校验第三批题库并生成冻结文件；不调用模型。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from dualpath.sandbox import grade, sandbox_support

from protocol import PROTOCOL

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FROZEN_FILES = ("protocol.py", "build_tasks.py", "tasks.json", "validate.py", "run.py", "analyze.py")


def tasks():
    result = json.loads((HERE / "tasks.json").read_text())
    assert len(result) == PROTOCOL["task_count"]
    assert [task["id"] for task in result] == [f"I{i:03}" for i in range(1, PROTOCOL["task_count"] + 1)]
    return result


def hashes():
    paths = [HERE / name for name in FROZEN_FILES] + sorted((ROOT / "src").rglob("*.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def main() -> None:
    if (HERE / "freeze.json").exists():
        raise SystemExit("freeze.json 已存在；冻结后禁止覆盖或修改题库")
    if not sandbox_support()["supported"]:
        raise SystemExit("沙箱不可用，无法校验题库")
    records = []
    for task in tasks():
        reference = grade(task["reference"], task["tests"], 10)
        mutants = [grade(mutant["code"], task["tests"], 10) for mutant in task["mutants"]]
        valid = reference["passed"] and all(m["status"] in {"fail", "timeout"} for m in mutants)
        records.append({"id": task["id"], "reference": reference, "mutants": mutants, "valid": valid})
        print(task["id"], "有效" if valid else "需要修正", flush=True)
    write(HERE / "validation.json", records)
    if not all(record["valid"] for record in records):
        raise SystemExit("参考实现或变异检查未通过，不能冻结")
    write(
        HERE / "freeze.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "protocol": PROTOCOL,
            "files": hashes(),
        },
    )
    print("已冻结", HERE / "freeze.json")


if __name__ == "__main__":
    main()
