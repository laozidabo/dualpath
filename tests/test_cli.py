"""v0.2 新增：CLI 的 .env 加载、参数校验、context 读取与 review/run 命令（全部离线）。"""

import argparse
import json
import os

import pytest

from dualpath.cli import load_context_files, load_env_file, main, positive_int
from dualpath.llm import DualPathError, LanguageModel
from dualpath.models import ReviewResult


def _make_review_result(status: str) -> ReviewResult:
    return ReviewResult(
        task="t",
        draft="草稿",
        findings=[],
        final_output="最终结果",
        status=status,
        model_calls=2,
        started_at="2026-10-31T00:00:00+00:00",
        finished_at="2026-10-31T00:00:01+00:00",
    )


def _fake_engine_class(status: str):
    class _FakeReviewEngine:
        def __init__(self, *, model, max_findings, reporter) -> None:
            self.model = model
            self.max_findings = max_findings
            self.reporter = reporter
            self.task: str | None = None
            self.context: dict[str, str] | None = None

        async def run(self, task: str, context: dict[str, str] | None = None) -> ReviewResult:
            self.task = task
            self.context = context
            return _make_review_result(status)

    return _FakeReviewEngine


class _FakeLegacyModel(LanguageModel):
    async def complete(self, system: str, user: str) -> str:
        if "[DIRECT_EXECUTE]" in system:
            return "直接线路输出"
        if "[ERROR_DISCOVER]" in system:
            return json.dumps(
                [{"title": "问题", "description": "说明", "exploration": "推演"}],
                ensure_ascii=False,
            )
        if "[ERROR_PROBABILITY]" in system:
            return json.dumps(
                {"probability": 0.5, "reasoning": "测试判断", "basis": "model_judgment"},
                ensure_ascii=False,
            )
        return "汇聚结果"


def test_load_env_file_only_fills_missing_variables(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# 注释行\n"
        "\n"
        "DUALPATH_TEST_MISSING=补充值\n"
        "DUALPATH_TEST_EXISTING=来自文件\n"
        "export DUALPATH_TEST_EXPORTED='带引号'\n"
        "DUALPATH_TEST_EQUALS=with=equals\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DUALPATH_TEST_EXISTING", "保留值")
    for key in ("DUALPATH_TEST_MISSING", "DUALPATH_TEST_EXPORTED", "DUALPATH_TEST_EQUALS"):
        monkeypatch.delenv(key, raising=False)

    try:
        load_env_file(env_path)

        assert os.environ["DUALPATH_TEST_MISSING"] == "补充值"
        assert os.environ["DUALPATH_TEST_EXISTING"] == "保留值"
        assert os.environ["DUALPATH_TEST_EXPORTED"] == "带引号"
        assert os.environ["DUALPATH_TEST_EQUALS"] == "with=equals"
    finally:
        for key in ("DUALPATH_TEST_MISSING", "DUALPATH_TEST_EXPORTED", "DUALPATH_TEST_EQUALS"):
            os.environ.pop(key, None)


def test_positive_int_rejects_zero_and_non_numeric() -> None:
    assert positive_int("3") == 3
    for raw in ("0", "x", "-1", "2.5"):
        with pytest.raises(argparse.ArgumentTypeError):
            positive_int(raw)


def test_load_context_files_rejects_missing_and_reads_existing(tmp_path) -> None:
    with pytest.raises(DualPathError):
        load_context_files([str(tmp_path / "missing.md")])
    with pytest.raises(DualPathError):
        load_context_files([str(tmp_path)])  # 目录也无法按文本读取

    spec = tmp_path / "spec.md"
    spec.write_text("资料甲", encoding="utf-8")
    assert load_context_files([str(spec)]) == {"spec.md": "资料甲"}


def test_main_review_without_api_key_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DUALPATH_PROVIDER", "deepseek")

    with pytest.raises(SystemExit) as excinfo:
        main(["review", "任务"])

    assert excinfo.value.code == 2


def test_main_review_writes_json_and_markdown_and_exits_0(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key-not-used-offline")
    monkeypatch.setattr("dualpath.cli.ReviewEngine", _fake_engine_class("completed"))
    json_path = tmp_path / "record.json"
    markdown_path = tmp_path / "record.md"

    # main 正常走到 code 0 时直接返回 None（不抛 SystemExit）。
    assert main(["review", "t", "--json", str(json_path), "--markdown", str(markdown_path)]) is None

    assert json_path.is_file()
    assert markdown_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["task"] == "t"
    assert "最终结果" in markdown_path.read_text(encoding="utf-8")


def test_main_review_failed_status_exits_2_but_still_saves_records(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key-not-used-offline")
    monkeypatch.setattr("dualpath.cli.ReviewEngine", _fake_engine_class("probe_failed"))
    json_path = tmp_path / "record.json"

    with pytest.raises(SystemExit) as excinfo:
        main(["review", "t", "--json", str(json_path)])

    assert excinfo.value.code == 2
    assert json_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "probe_failed"


def test_main_run_prints_deprecation_warning(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dualpath.cli.create_model", lambda args: _FakeLegacyModel())

    assert main(["run", "任务", "--no-search"]) is None

    captured = capsys.readouterr()
    # 实际文案为「警告：`dualpath run` … 将在 v0.3 移除」：语义上就是弃用警告。
    assert "警告" in captured.err
    assert "dualpath run" in captured.err
    assert "v0.3" in captured.err
    assert "review" in captured.err
    assert "最终结果" in captured.out
