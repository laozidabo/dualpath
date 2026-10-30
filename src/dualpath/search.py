"""可替换的互联网搜索接口。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 4) -> list[SearchResult]:
        """搜索互联网资料。"""


class DuckDuckGoSearch(SearchProvider):
    async def search(self, query: str, max_results: int = 4) -> list[SearchResult]:
        from ddgs import DDGS

        def run_search() -> list[SearchResult]:
            with DDGS() as client:
                rows = client.text(query, max_results=max_results)
                return [
                    SearchResult(
                        title=row.get("title", ""),
                        url=row.get("href", ""),
                        snippet=row.get("body", ""),
                    )
                    for row in rows
                    if row.get("href")
                ]

        return await asyncio.to_thread(run_search)


class NoSearch(SearchProvider):
    async def search(self, query: str, max_results: int = 4) -> list[SearchResult]:
        return []
