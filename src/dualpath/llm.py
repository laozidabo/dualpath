"""模型抽象、OpenAI 兼容接口与严格 JSON 解析。"""

from __future__ import annotations

import json
import math
import os
import re
from abc import ABC, abstractmethod
from typing import Any

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 60.0


class DualPathError(Exception):
    """可预期的运行错误；CLI 会把它转成一行提示并以退出码 2 结束。"""


class ModelError(DualPathError):
    """模型请求失败。"""


class ModelTimeout(ModelError):
    """模型请求超时。"""


class ModelRequestRejected(ModelError):
    """接口拒绝了请求（例如不支持 JSON 输出模式）。"""


class ModelResponseError(ModelError):
    """模型返回内容不可用：截断、非法 JSON 或缺少正文。"""


class JsonProtocolError(ValueError):
    """JSON 不满足严格协议。"""


def _scrub(message: str, limit: int = 400) -> str:
    return re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<redacted>", message)[:limit]


def _map_model_error(error: BaseException) -> ModelError:
    name = type(error).__name__
    if name in {"APITimeoutError", "TimeoutError", "ReadTimeout", "ConnectTimeout"}:
        return ModelTimeout(f"模型请求超时（{name}）")
    if name in {"APIConnectionError", "ConnectionError"}:
        return ModelError(f"无法连接模型接口（{name}）")
    if name in {"BadRequestError", "UnprocessableEntityError"}:
        return ModelRequestRejected(f"接口拒绝了请求（{name}）：{_scrub(str(error))}")
    return ModelError(f"模型请求失败（{name}）：{_scrub(str(error))}")


class LanguageModel(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str) -> str:
        """完成一次无状态模型调用。"""


class OpenAICompatibleModel(LanguageModel):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.2,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.client = AsyncOpenAI(
            api_key=api_key or os.getenv("DUALPATH_API_KEY") or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("DUALPATH_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
            timeout=timeout,
            max_retries=0,
        )

    async def complete(self, system: str, user: str) -> str:
        return await self._request(system, user, json_mode=False)

    async def complete_json(self, system: str, user: str) -> str:
        """优先使用接口的 JSON 输出模式；接口不支持时退回普通模式。"""
        try:
            return await self._request(system, user, json_mode=True)
        except ModelRequestRejected:
            return await self._request(system, user, json_mode=False)

    async def _request(self, system: str, user: str, *, json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = await self.client.chat.completions.create(**payload)
        except Exception as error:  # noqa: BLE001 - 统一映射为带类型的模型错误
            raise _map_model_error(error) from None
        try:
            choice = response.choices[0]
            content = choice.message.content
        except (AttributeError, IndexError, TypeError) as error:
            raise ModelResponseError("模型响应缺少正文") from error
        # 兼容没有结束标记的旧接口；明确的截断、过滤或工具调用不得当作交付结果。
        if getattr(choice, "finish_reason", None) not in {None, "stop"}:
            raise ModelResponseError("模型响应未完整结束")
        if not isinstance(content, str) or not content.strip():
            raise ModelResponseError("模型响应正文为空或不是文本")
        return content


class DeepSeekModel(OpenAICompatibleModel):
    """DeepSeek 官方 API，使用其 OpenAI 兼容接口。"""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.2,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            base_url=base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            temperature=temperature,
            timeout=timeout,
        )


def extract_json(text: str) -> Any:
    """从纯 JSON 或 Markdown 代码块中提取 JSON（宽松，兼容旧流程）。"""

    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(stripped):
            if character not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[index:])
                return value
            except json.JSONDecodeError:
                continue
        raise ValueError(f"模型没有返回有效 JSON：{text[:300]}") from None


def parse_strict_json(text: str) -> Any:
    """严格解析：只接受完整 JSON 或一个完整围栏，拒绝截断、重复键与非有限数字。"""
    if not isinstance(text, str) or len(text.encode()) > 32_768:
        raise JsonProtocolError("JSON 文本过长或类型错误")
    text = text.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
        if not match:
            raise JsonProtocolError("不完整的 JSON 围栏")
        text = match.group(1)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise JsonProtocolError("重复的 JSON 键")
            result[key] = value
        return result

    def constant(_):
        raise JsonProtocolError("非有限 JSON 数字")

    def finite_float(raw: str) -> float:
        number = float(raw)
        if not math.isfinite(number):
            raise JsonProtocolError("非有限 JSON 数字")
        return number

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)
    except (ValueError, RecursionError) as error:
        raise JsonProtocolError("不是完整且唯一的 JSON 文档") from error


async def _request_json_text(model: LanguageModel, system: str, user: str) -> str:
    method = getattr(model, "complete_json", None)
    if callable(method):
        return await method(system, user)
    return await model.complete(system, user)


async def complete_json(
    model: LanguageModel,
    system: str,
    user: str,
    *,
    retries: int = 1,
    strict: bool = False,
) -> Any:
    """请求 JSON；格式失败时最多重问 ``retries`` 次。"""
    parser = parse_strict_json if strict else extract_json
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        prompt = system if attempt == 0 else (
            system + "\n\n[格式重试] 上一次回复不是有效 JSON。请只输出一个完整、可解析的 JSON 值，"
            "不要输出 Markdown 代码块、解释或省略号。"
        )
        text = await _request_json_text(model, prompt, user)
        try:
            return parser(text)
        except (ValueError, RecursionError) as error:
            last_error = error
    raise ModelResponseError(f"模型没有返回有效 JSON：{last_error}") from None
