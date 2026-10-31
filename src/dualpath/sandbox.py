"""Grade untrusted Python in a mandatory, isolated Linux bubblewrap sandbox.

Hidden tests are trusted harness input. They may be plain assertions referring to
solution names, or a unittest module importing ``solution``. Neither tests nor
references are sent to a model. Only solution.py, tests.py and runner.py are
mounted, read-only, alongside system runtime files. No host home/repo, environment
or network is exposed. Unsupported isolation fails closed.

This is process/filesystem isolation, not protection against a deliberately
adversarial solution tampering with Python's in-process testing machinery.
"""

from __future__ import annotations

import ast
import functools
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
OUTPUT_LIMIT_BYTES = 1024 * 1024
_ROOT = Path(__file__).resolve().parent
_PYTHON = "/usr/bin/python3"


def extract_code(text: str) -> str:
    """Take the longest Python fence; ties select the first, never concatenate.

    Python/py/python3 tags (case-insensitive) have priority over unlabelled
    fences. If no eligible complete fence exists, preserve the original text.
    This avoids appending a later usage example to an otherwise valid module.
    Both backtick and tilde Markdown fences of length >= 3 are supported.
    """
    if not isinstance(text, str):
        raise TypeError("model output must be text")
    blocks: list[tuple[str, str]] = []
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        opening = re.fullmatch(r"[ \t]{0,3}(`{3,}|~{3,})[ \t]*([^\r\n]*)[\r\n]*", lines[index])
        if not opening:
            index += 1
            continue
        fence, language = opening.groups()
        end = index + 1
        closing = re.compile(r"[ \t]{0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*[\r\n]*")
        while end < len(lines) and not closing.fullmatch(lines[end]):
            end += 1
        if end == len(lines):
            break
        blocks.append((language.strip().lower(), "".join(lines[index + 1:end])))
        index = end + 1
    eligible = [body for lang, body in blocks if lang in {"python", "python3", "py"}]
    if not eligible:
        eligible = [body for lang, body in blocks if not lang]
    return max(eligible, key=len) if eligible else text


def _sandbox_command(bwrap: str, directory: Path | None, child: list[str]) -> list[str]:
    command = [
        bwrap, "--unshare-all", "--unshare-user", "--unshare-pid",
        "--unshare-net", "--disable-userns", "--assert-userns-disabled",
        "--die-with-parent", "--new-session", "--cap-drop", "ALL",
        "--clearenv", "--ro-bind", "/usr", "/usr",
    ]
    for directory_name in ("/lib", "/lib64"):
        if os.path.islink(directory_name):
            target = os.readlink(directory_name)
            resolved = Path(directory_name).resolve()
            if not resolved.is_relative_to("/usr"):
                raise RuntimeError(f"unsupported runtime symlink: {directory_name}")
            command += ["--symlink", target, directory_name]
        elif os.path.isdir(directory_name):
            command += ["--ro-bind", directory_name, directory_name]
    command += [
        "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev",
        "--size", str(16 * 1024 * 1024), "--tmpfs", "/tmp", "--dir", "/work",
    ]
    if directory is not None:
        # Never bind the containing workspace or even the complete temp folder.
        for filename in ("solution.py", "tests.py", "runner.py"):
            command += ["--ro-bind", str(directory / filename), f"/work/{filename}"]
    return command + [
        "--chdir", "/work", "--setenv", "PATH", "/usr/bin",
        "--setenv", "LANG", "C.UTF-8", "--setenv", "HOME", "/nonexistent",
        "--setenv", "TMPDIR", "/tmp", "--", *child,
    ]


@functools.lru_cache(maxsize=1)
def sandbox_support() -> dict[str, Any]:
    """Probe required isolation once; unavailable or denied bwrap is unsupported."""
    bwrap = shutil.which("bwrap")
    if not bwrap or not os.path.isfile(_PYTHON):
        return {"supported": False, "detail": "sandbox_unsupported: bwrap or /usr/bin/python3 is missing"}
    try:
        command = _sandbox_command(bwrap, None, [_PYTHON, "-I", "-c", "print('dualpath-sandbox-ready')"])
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                env={}, timeout=5, close_fds=True, start_new_session=True)
        if result.returncode != 0 or result.stdout.strip() != b"dualpath-sandbox-ready":
            diagnostic = result.stderr.decode("utf-8", errors="replace")[:2000]
            return {"supported": False, "detail": f"sandbox_unsupported: probe failed: {diagnostic}"}
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return {"supported": False, "detail": f"sandbox_unsupported: {type(error).__name__}: {error}"}
    return {"supported": True, "bwrap": bwrap, "detail": "isolated bwrap probe passed"}


def _runner_source(marker: str, timeout: float, assertion_count: int) -> str:
    # Bind trusted output helpers before importing generated code. In particular,
    # merely calling os._exit(0) or printing PASS never produces a passing grade.
    return f'''import ast, contextlib, importlib, io, json, math, os, resource, sys, traceback, types, unittest
_write, _dumps, _exit = os.write, json.dumps, os._exit
_marker = {marker!r}
_status = "infrastructure_error"
def _finish(status, detail):
    message = {{"status": status, "detail": str(detail)[:12000]}}
    _write(1, ("\\n" + _marker + _dumps(message, ensure_ascii=True) + "\\n").encode())
    _exit(0)
try:
    resource.setrlimit(resource.RLIMIT_AS, ({MEMORY_LIMIT_BYTES}, {MEMORY_LIMIT_BYTES}))
    resource.setrlimit(resource.RLIMIT_CPU, ({max(1, math.ceil(timeout))}, {max(2, math.ceil(timeout) + 1)}))
    resource.setrlimit(resource.RLIMIT_FSIZE, ({OUTPUT_LIMIT_BYTES}, {OUTPUT_LIMIT_BYTES}))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # This benchmark evaluates single-process functions. Forbid descendants so
    # candidate forks cannot multiply the per-process address-space allowance.
    resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    sys.dont_write_bytecode = True
    sys.path.insert(0, "/work")
    test_source = open("/work/tests.py", encoding="utf-8").read()
    test_code = compile(test_source, "/work/tests.py", "exec")
    _status = "fail"
    # Suppress candidate printing so accidental stdout cannot fill the protocol
    # log; direct low-level writes are separately bounded by RLIMIT_FSIZE.
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        solution = importlib.import_module("solution")
        test_module = types.ModuleType("candidate_tests")
        test_module.__dict__.update(solution.__dict__)
        test_module.__dict__.update(__name__="candidate_tests", __file__="/work/tests.py", solution=solution)
        exec(test_code, test_module.__dict__)
        suite = unittest.defaultTestLoader.loadTestsFromModule(test_module)
        count = suite.countTestCases()
        if count:
            result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
            successful = result.wasSuccessful() and result.testsRun > 0 and len(result.skipped) < result.testsRun
            detail = {{"tests_run": result.testsRun, "failures": len(result.failures),
                     "errors": len(result.errors), "skipped": len(result.skipped)}}
            if result.failures or result.errors:
                detail["first_failure"] = (result.failures + result.errors)[0][1][-6000:]
            _finish("pass" if successful else "fail", detail)
        elif {assertion_count} > 0:
            _finish("pass", "all trusted assertion tests completed")
        else:
            _finish("infrastructure_error", "hidden tests contain no assertions or runnable unittest cases")
except BaseException as error:
    try:
        diagnostic = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-10000:]
    except BaseException:
        diagnostic = "candidate raised an exception"
    _finish(_status, diagnostic)
'''


def grade(code: str, tests: str, timeout: float = 5) -> dict[str, Any]:
    """Return pass/fail/timeout/infrastructure_error without unsandboxed execution.

    ``code`` is already extracted source; call ``extract_code`` for model text.
    Syntax-invalid hidden tests are infrastructure errors. Candidate syntax,
    import, assertion, resource-limit and early-exit failures do not pass.
    """
    started = time.monotonic()

    def outcome(status: str, detail: str) -> dict[str, Any]:
        return {"status": status, "passed": status == "pass",
                "duration_seconds": round(time.monotonic() - started, 6), "detail": detail}

    if not isinstance(code, str) or not isinstance(tests, str):
        return outcome("infrastructure_error", "code and tests must be strings")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        return outcome("infrastructure_error", "timeout must be a finite positive number")
    try:
        test_tree = ast.parse(tests, filename="tests.py")
    except SyntaxError as error:
        return outcome("infrastructure_error", f"invalid hidden tests: {error}")
    assertion_count = sum(isinstance(node, ast.Assert) for node in ast.walk(test_tree))
    support = sandbox_support()
    if not support["supported"]:
        return outcome("infrastructure_error", support["detail"])
    marker = "DUALPATH_RESULT_" + secrets.token_hex(24) + ":"
    try:
        # 使用系统临时目录，安装目录可以是只读的。
        with tempfile.TemporaryDirectory(prefix="dualpath-verify-") as temp:
            directory = Path(temp)
            (directory / "solution.py").write_text(code, encoding="utf-8")
            (directory / "tests.py").write_text(tests, encoding="utf-8")
            (directory / "runner.py").write_text(_runner_source(marker, timeout, assertion_count), encoding="utf-8")
            command = _sandbox_command(support["bwrap"], directory, [_PYTHON, "-I", "/work/runner.py"])
            # A regular, size-limited output file avoids unbounded PIPE buffering.
            with tempfile.TemporaryFile(dir=directory) as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=log, env={}, close_fds=True, start_new_session=True)
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=2)
                    return outcome("timeout", f"wall-clock limit exceeded ({timeout:g}s)")
                log.seek(0)
                output = log.read(OUTPUT_LIMIT_BYTES + 1).decode("utf-8", errors="replace")
            records = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
            if returncode == 0 and len(records) == 1:
                try:
                    record = json.loads(records[0])
                    if record["status"] in {"pass", "fail", "infrastructure_error"}:
                        return outcome(record["status"], str(record.get("detail", "")))
                except (ValueError, TypeError, KeyError):
                    pass
            if returncode in (-signal.SIGXCPU, 128 + signal.SIGXCPU):
                return outcome("timeout", "CPU time limit exceeded")
            if "bwrap:" in output:
                return outcome("infrastructure_error", "sandbox startup/execution failed: " + output[-2000:])
            return outcome(
                "fail",
                f"candidate exited without a valid completed-test result (exit {returncode}): " + output[-2000:],
            )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return outcome("infrastructure_error", f"sandbox execution error: {type(error).__name__}: {error}")
