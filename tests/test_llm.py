import pytest

from dualpath.llm import extract_json


def test_extract_json_from_code_fence() -> None:
    assert extract_json('```json\n{"probability": 0.6}\n```') == {"probability": 0.6}


def test_extract_json_from_surrounding_text() -> None:
    assert extract_json('结果如下：\n[{"title": "问题"}]') == [{"title": "问题"}]


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(ValueError):
        extract_json("没有 JSON")
