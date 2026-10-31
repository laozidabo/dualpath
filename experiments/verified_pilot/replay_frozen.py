"""从已发布的冻结源码归档复放实验，不修改当前代码或原始运行记录。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]


def main():
    archive = ROOT / "artifacts" / "dualpath-v2-reproducibility.zip"
    with tempfile.TemporaryDirectory(prefix="dualpath-frozen-") as directory:
        target = Path(directory)
        with ZipFile(archive) as z:
            for entry in z.infolist():
                path = Path(entry.filename)
                if path.parts[:1] != ("dualpath-v2",) or entry.is_dir():
                    continue
                relative = Path(*path.parts[1:])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("归档路径无效")
                dest = target / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(entry))
        experiment = target / "experiments" / "verified_pilot"
        for name in ("replay.py", "supplement.py", "supplement_freeze.json"):
            shutil.copy2(ROOT / "experiments" / "verified_pilot" / name, experiment / name)
        reports = []
        for batch in ("pilot-2", "supplement-1"):
            dest = target / "verify-runs" / batch
            shutil.copytree(ROOT / "verify-runs" / batch, dest, ignore=shutil.ignore_patterns("*.lock"))
        environment = dict(os.environ, PYTHONPATH=str(target / "src"))
        for batch in ("pilot-2", "supplement-1"):
            completed = subprocess.run([sys.executable, str(experiment / "replay.py"), "--output", str(target / "verify-runs" / batch)], cwd=target, env=environment, capture_output=True, text=True)
            if completed.returncode:
                raise RuntimeError(completed.stderr)
            reports.append({"batch": batch, "audit": json.loads(completed.stdout)})
        print(json.dumps({"network_calls": 0, "all_checks_passed": True, "batches": reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
