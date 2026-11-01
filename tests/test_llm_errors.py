"""v0.2 新增：严格 JSON 解析、模型错误映射与 complete_json 重试（全部离线）。"""

from types import SimpleNamespace

import pytest

from dualpath.llm import (
    JsonProtocolError,
    LanguageModel,
    ModelError,
    ModelRequestRejected,
    ModelResponseError,
    ModelTimeout,
    OpenAICompatibleModel,
    complete_json,
    parse_strict_json,
)


def _response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _FakeCompletions:
    def __init__(self, handler) -> None:
        self._handler = handler
        self.payloads: list[dict] = []

    async def create(self, **payload):
        self.payloads.append(payload)
        return await self._handler(payload)


class _FakeClient:
    def __init__(self, handler) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(handler))


def _make_model(handler) -> OpenAICompatibleModel:
    """绕过 __init__ 里的真实客户端构造，只用假 client 离线测试 complete。"""
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model.model = "fake-model"
    model.temperature = 0.2
    model.timeout = 1.0
    model.client = _FakeClient(handler)
    return model


class _SequenceModel(LanguageModel):
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.systems: list[str] = []
        self.calls = 0

    async def complete(self, system: str, user: str) -> str:
        self.systems.append(system)
        self.calls += 1
        return self.outputs[self.calls - 1]


def test_parse_strict_json_rejects_protocol_violations() -> None:
    malformed = [
        '{"a": 1, "a": 2}',  # 重复键
        '{"a": NaN}',  # 非有限数字
        '{"a": Infinity}',
        '{"a": -Infinity}',
        "[1, 2",  # 截断数组
        '{"a": 1} 多余内容',
        '```json\n{"a": 1}',  # 不完整围栏
        "没有任何 JSON",
    ]
    for text in malformed:
        with pytest.raises(JsonProtocolError):
            parse_strict_json(text)


def test_parse_strict_json_accepts_complete_code_fence() -> None:
    assert parse_strict_json('```json\n{"findings": []}\n```') == {"findings": []}
    assert parse_strict_json('  [1, 2, {"k": 3.5}]  ') == [1, 2, {"k": 3.5}]


async def test_openai_compatible_model_wraps_client_errors_and_redacts_key() -> None:
    async def handler(payload):
        raise RuntimeError("上游失败 sk-abcdefgh12345678 结束")

    model = _make_model(handler)
    with pytest.raises(ModelError) as excinfo:
        await model.complete("system", "user")

    message = str(excinfo.value)
    assert "模型请求失败（RuntimeError）" in message
    assert "<redacted>" in message
    assert "sk-abcdefgh12345678" not in message
    assert not isinstance(excinfo.value, (ModelTimeout, ModelRequestRejected, ModelResponseError))


async def test_openai_compatible_model_maps_timeout_error() -> None:
    class APITimeoutError(Exception):
        pass

    async def handler(payload):
        raise APITimeoutError("timed out")

    model = _make_model(handler)
    with pytest.raises(ModelTimeout):
        await model.complete("system", "user")


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls"])
async def test_incomplete_response_is_rejected_even_with_valid_text(finish_reason) -> None:
    async def handler(payload):
        response = _response('{"findings": []}')
        response.choices[0].finish_reason = finish_reason
        return response

    with pytest.raises(ModelResponseError, match="未完整结束"):
        await _make_model(handler).complete("system", "user")


@pytest.mark.parametrize("content", ["", " \n\t"])
async def test_empty_response_is_rejected(content) -> None:
    async def handler(payload):
        return _response(content)

    with pytest.raises(ModelResponseError, match="为空"):
        await _make_model(handler).complete("system", "user")


async def test_complete_json_falls_back_when_json_mode_is_rejected() -> None:
    class BadRequestError(Exception):
        pass

    async def handler(payload):
        if "response_format" in payload:
            raise BadRequestError("response_format 不支持")
        return _response('{"ok": true}')

    model = _make_model(handler)
    raw = await model.complete_json("system", "user")

    assert raw == '{"ok": true}'
    payloads = model.client.chat.completions.payloads
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]


async def test_complete_json_retries_invalid_json_then_raises_after_repeated_failure() -> None:
    model = _SequenceModel(["不是 JSON", '{"ok": true}'])
    assert await complete_json(model, "system", "user", retries=1) == {"ok": True}
    assert model.calls == 2
    assert "[格式重试]" in model.systems[1]

    broken = _SequenceModel(["仍然不是 JSON", "还是不是 JSON"])
    with pytest.raises(ModelResponseError):
        await complete_json(broken, "system", "user", retries=1)
    assert broken.calls == 2
