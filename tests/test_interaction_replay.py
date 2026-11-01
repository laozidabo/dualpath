"""复放必须拒绝请求漂移和归档损坏，不得悄悄使用不匹配的历史响应。"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "interaction_replay", ROOT / "experiments/interaction_pilot/replay_frozen.py",
)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def tape():
    return replay.ReplayTape([{
        "status": "ok", "group": "I001/direct", "request": {"messages": ["原请求"]},
        "response": {"choices": [{"message": {"content": "原响应"}}]},
    }])


@pytest.mark.parametrize("group,payload", [
    ("I002/direct", {"messages": ["原请求"]}),
    ("I001/direct", {"messages": ["变更请求"]}),
])
def test_replay_rejects_request_or_group_drift(group, payload):
    recorded = tape()
    with pytest.raises(RuntimeError, match="不一致"):
        recorded.reply(group, payload)
    assert recorded.position == 0
    with pytest.raises(RuntimeError, match="存在差异"):
        recorded.finish()


def test_replay_requires_exact_number_of_calls_and_isolates_responses():
    recorded = tape()
    with pytest.raises(RuntimeError, match="没有全部消费"):
        recorded.finish()
    response = recorded.reply("I001/direct", {"messages": ["原请求"]})
    response["choices"].clear()
    assert recorded.calls[0]["response"]["choices"]
    recorded.finish()
    with pytest.raises(RuntimeError, match="额外请求"):
        recorded.reply("I001/direct", {"messages": ["原请求"]})
    with pytest.raises(RuntimeError, match="存在差异"):
        recorded.finish()


def test_archive_tampering_is_rejected_before_extraction(tmp_path):
    archive = tmp_path / "tampered.zip"
    original = (ROOT / "artifacts/interaction-pilot-frozen-source.zip").read_bytes()
    archive.write_bytes(original + b"tampered")
    destination = tmp_path / "source"
    with pytest.raises(RuntimeError, match="归档散列不一致"):
        replay.restore(archive, destination)
    assert not destination.exists()


def test_frozen_archive_restores_only_manifest_files(tmp_path):
    frozen = replay.restore(ROOT / "artifacts/interaction-pilot-frozen-source.zip", tmp_path)
    actual = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()}
    assert actual == set(frozen["files"]) | {replay.FREEZE_PATH}


def test_comparison_preserves_scientific_results_while_ignoring_runtime():
    original = {"duration_seconds": 1.0, "results": [{"passed": True, "duration_seconds": 2.0}]}
    replayed = {"duration_seconds": 3.0, "results": [{"passed": True, "duration_seconds": 4.0}]}
    assert replay.stable(original) == replay.stable(replayed)
    replayed["results"][0]["passed"] = False
    assert replay.stable(original) != replay.stable(replayed)
