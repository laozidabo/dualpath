"""模型抽象与 OpenAI 兼容接口。"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any


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
    ) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self.temperature = temperature
        self.client = AsyncOpenAI(
            api_key=api_key or os.getenv("DUALPATH_API_KEY") or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("DUALPATH_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
        )

    async def complete(self, system: str, user: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class DeepSeekModel(OpenAICompatibleModel):
    """DeepSeek 官方 API，使用其 OpenAI 兼容接口。"""

    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            base_url=base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            temperature=temperature,
        )


def extract_json(text: str) -> Any:
    """从纯 JSON 或 Markdown 代码块中提取 JSON。"""

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
        raise ValueError(f"模型没有返回有效 JSON：{text[:300]}")


async def complete_json(model: LanguageModel, system: str, user: str) -> Any:
    return extract_json(await model.complete(system, user))
