"""Safety and grading contract checks; integration checks need real bwrap."""
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dualpath import sandbox as evaluate

ROOT = Path(__file__).resolve().parents[1]


class ExtractionTests(unittest.TestCase):
    def test_python_priority_longest_and_no_concatenation(self):
        text = "```\nlong_unlabelled = '" + "x" * 100 + "'\n```\n```python\nx=1\n```\n```py\nanswer = 42\n```\n"
        self.assertEqual(evaluate.extract_code(text), "answer = 42\n")

    def test_first_tie_and_tilde_fence(self):
        self.assertEqual(evaluate.extract_code("~~~Python\na=1\n~~~\n```python\nb=2\n```"), "a=1\n")

    def test_no_eligible_fence_preserves_text(self):
        for text in ("x=2\n", "```javascript\nlet x=2;\n```", "```python\nx=2"):
            self.assertEqual(evaluate.extract_code(text), text)

    def test_unlabelled_fallback(self):
        self.assertEqual(evaluate.extract_code("Here:\n```\ndef f():\n    return 3\n```"), "def f():\n    return 3\n")


class FailClosedTests(unittest.TestCase):
    def test_unsupported_never_executes_candidate(self):
        with (
            mock.patch.object(
                evaluate,
                "sandbox_support",
                return_value={"supported": False, "detail": "sandbox_unsupported: test"},
            ),
            mock.patch.object(evaluate.subprocess, "Popen") as popen,
        ):
            result = evaluate.grade("raise RuntimeError('must not execute')", "assert True")
        self.assertEqual(result["status"], "infrastructure_error")
        popen.assert_not_called()

    def test_bad_hidden_tests_and_timeout_are_infrastructure_errors(self):
        self.assertEqual(evaluate.grade("x=1", "assert (", 1)["status"], "infrastructure_error")
        for timeout in (0, -1, float("nan"), float("inf")):
            self.assertEqual(evaluate.grade("x=1", "assert True", timeout)["status"], "infrastructure_error")


@unittest.skipUnless(evaluate.sandbox_support()["supported"], "bwrap isolation unavailable")
class SandboxTests(unittest.TestCase):
    def test_assertion_pass_and_fail(self):
        self.assertEqual(evaluate.grade("def add(a,b): return a+b", "assert add(2,3)==5")["status"], "pass")
        self.assertEqual(evaluate.grade("def add(a,b): return a-b", "assert add(2,3)==5")["status"], "fail")

    def test_unittest_pass_and_fail(self):
        tests = (
            "import unittest\nimport solution\n"
            "class TestIt(unittest.TestCase):\n"
            " def test_answer(self): self.assertEqual(solution.answer(), 42)\n"
        )
        self.assertEqual(evaluate.grade("def answer(): return 42", tests)["status"], "pass")
        self.assertEqual(evaluate.grade("def answer(): return 0", tests)["status"], "fail")

    def test_syntax_error_early_exit_and_empty_tests_cannot_pass(self):
        for code in ("def broken(:", "import os; os._exit(0)", "raise SystemExit(0)"):
            self.assertEqual(evaluate.grade(code, "assert True")["status"], "fail")
        self.assertEqual(evaluate.grade("x=1", "x=2")["status"], "infrastructure_error")

    def test_timeout(self):
        result = evaluate.grade("while True: pass", "assert True", timeout=0.3)
        self.assertEqual(result["status"], "timeout")
        self.assertLess(result["duration_seconds"], 3)

    def test_memory_limit(self):
        result = evaluate.grade("x = bytearray(1024*1024*1024)", "assert True")
        self.assertEqual(result["status"], "fail")
        self.assertIn("MemoryError", result["detail"])

    def test_descendants_cannot_multiply_memory_allowance(self):
        code = """import os
try:
    pid = os.fork()
except OSError:
    blocked = True
else:
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    blocked = False
"""
        self.assertEqual(evaluate.grade(code, "assert blocked")["status"], "pass")

    def test_host_file_environment_and_tests_are_isolated(self):
        with tempfile.NamedTemporaryFile(dir=ROOT, mode="w") as host_file:
            host_file.write("host-secret")
            host_file.flush()
            with mock.patch.dict(os.environ, {"DUALPATH_HOST_SECRET": "do-not-expose"}):
                code = f"""import os
assert 'DUALPATH_HOST_SECRET' not in os.environ
assert not os.path.exists({host_file.name!r})
assert not os.path.exists({str(ROOT)!r})
assert not os.path.exists('/home')
assert set(os.listdir('/work')) == {{'solution.py', 'tests.py', 'runner.py'}}
try:
    open('/work/tests.py', 'w')
except OSError:
    pass
else:
    raise AssertionError('tests writable')
value = 42
"""
                result = evaluate.grade(code, "assert value == 42")
        self.assertEqual(result["status"], "pass", result)

    def test_host_loopback_is_unreachable(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            code = f"""import socket
s = socket.socket()
s.settimeout(0.5)
assert s.connect_ex(('127.0.0.1', {port})) != 0
assert [name for _, name in socket.if_nameindex()] == ['lo']
"""
            self.assertEqual(evaluate.grade(code, "assert True")["status"], "pass")

    def test_output_flood_is_bounded(self):
        result = evaluate.grade("import os\nwhile True: os.write(1, b'x'*65536)", "assert True", timeout=2)
        self.assertIn(result["status"], {"fail", "timeout"})
        self.assertLess(len(result["detail"]), 4000)


if __name__ == "__main__":
    unittest.main()
