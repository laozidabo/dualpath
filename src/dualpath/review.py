"""失败先行、证据驱动的 review 模式：草稿 + 失败发现 + 盲审 + 有证据才修正。"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .llm import (
    JsonProtocolError,
    LanguageModel,
    ModelError,
    ModelResponseError,
    parse_strict_json,
)
from .models import CHECK_KINDS, Finding, ReviewResult, utc_now
from .prompts import REVIEW_DECISIONS, REVIEW_DRAFT, REVIEW_FINAL, REVIEW_PROBE
from .reporting import ReviewConsoleReporter

MAX_CONTEXT_FILE_BYTES = 32 * 1024
MAX_CONTEXT_BYTES = 64 * 1024
MAX_FINDINGS = 10

_ID_RE = re.compile(r"F[1-9][0-9]?")
_WHITESPACE = re.compile(r"\s+")
_FINDING_FIELDS = {
    "id",
    "title",
    "mechanism",
    "trigger",
    "rule_quote",
    "check_kind",
    "check",
    "expected_signal",
}
_DECISION_FIELDS = {"id", "verdict", "reason"}
_DECISION_TO_STATUS = {"accept": "supported", "ambiguous": "ambiguous", "reject": "rejected"}

Ask = Callable[[str, str], Awaitable[str]]


class ReviewFormatError(ValueError):
    """模型返回的 review 结构不符合协议。"""


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _require_text(value: Any, field: str, *, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewFormatError(f"{field} 必须是非空字符串")
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise ReviewFormatError(f"{field} 超过 {limit} 字")
    return cleaned


def _clean_context(context: dict[str, str] | None) -> dict[str, str]:
    if context is None:
        return {}
    if not isinstance(context, dict):
        raise ValueError("context 必须是文件名到文本的映射")
    cleaned: dict[str, str] = {}
    total = 0
    for name, text in context.items():
        if not isinstance(name, str) or not isinstance(text, str):
            raise ValueError("context 必须是文件名到文本的映射")
        size = len(text.encode())
        if size > MAX_CONTEXT_FILE_BYTES:
            raise ValueError(f"context 文件过大（>{MAX_CONTEXT_FILE_BYTES} 字节）：{name}")
        total += size
        cleaned[name] = text
    if total > MAX_CONTEXT_BYTES:
        raise ValueError(f"context 总大小超过 {MAX_CONTEXT_BYTES} 字节")
    return cleaned


def _corpus(task: str, context: dict[str, str]) -> list[tuple[str, str]]:
    items = [("task", _normalize(task))]
    items.extend((name, _normalize(text)) for name, text in context.items())
    return items


def _locate_quote(quote: str, corpus: list[tuple[str, str]]) -> str:
    normalized = _normalize(quote)
    if len(normalized) < 8:
        raise ReviewFormatError("rule_quote 至少需要 8 个字符")
    for source, text in corpus:
        if normalized in text:
            return source
    raise ReviewFormatError("rule_quote 不是任务或资料中的原文片段")


def parse_findings(text: str, *, max_findings: int, corpus: list[tuple[str, str]]) -> list[Finding]:
    """解析并严格校验失败清单；任何一条不合格都拒绝整批。"""
    data = parse_strict_json(text)
    if not isinstance(data, dict) or set(data) != {"findings"} or not isinstance(data["findings"], list):
        raise ReviewFormatError('失败清单必须是 {"findings": [...]}')
    rows = data["findings"]
    if len(rows) > max_findings:
        raise ReviewFormatError(f"失败清单数量不能超过 {max_findings}")
    if not rows:
        return []

    findings: list[Finding] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _FINDING_FIELDS:
            raise ReviewFormatError("每条失败模式必须包含且只包含规定字段")
        finding_id = row["id"]
        if not isinstance(finding_id, str) or not _ID_RE.fullmatch(finding_id) or finding_id in seen:
            raise ReviewFormatError("失败模式 id 必须唯一且形如 F1")
        seen.add(finding_id)
        check_kind = row["check_kind"]
        if not isinstance(check_kind, str) or check_kind not in CHECK_KINDS:
            raise ReviewFormatError("check_kind 只能是 executable 或 manual")
        quote = _require_text(row["rule_quote"], "rule_quote", limit=500)
        source = _locate_quote(quote, corpus)
        findings.append(
            Finding(
                id=finding_id,
                title=_require_text(row["title"], "title", limit=120),
                mechanism=_require_text(row["mechanism"], "mechanism"),
                trigger=_require_text(row["trigger"], "trigger"),
                rule_quote=quote,
                rule_source=source,
                check_kind=check_kind,
                check=_require_text(row["check"], "check", limit=1000),
                expected_signal=_require_text(row["expected_signal"], "expected_signal"),
            )
        )
    return findings


def parse_decisions(text: str, findings: list[Finding]) -> dict[str, tuple[str, str]]:
    """解析盲审结果；必须恰好覆盖全部失败模式。"""
    data = parse_strict_json(text)
    if not isinstance(data, dict) or set(data) != {"decisions"} or not isinstance(data["decisions"], list):
        raise ReviewFormatError('审阅结果必须是 {"decisions": [...]}')
    expected = {finding.id for finding in findings}
    decisions: dict[str, tuple[str, str]] = {}
    for row in data["decisions"]:
        if not isinstance(row, dict) or set(row) != _DECISION_FIELDS:
            raise ReviewFormatError("每条审阅必须包含且只包含 id/verdict/reason")
        finding_id = row["id"]
        if not isinstance(finding_id, str) or finding_id not in expected:
            raise ReviewFormatError("审阅覆盖了未知的失败模式 id")
        if finding_id in decisions:
            raise ReviewFormatError("审阅中的 id 重复")
        verdict = row["verdict"]
        if not isinstance(verdict, str) or verdict not in _DECISION_TO_STATUS:
            raise ReviewFormatError("verdict 只能是 accept/reject/ambiguous")
        decisions[finding_id] = (verdict, _require_text(row["reason"], "reason"))
    if set(decisions) != expected:
        raise ReviewFormatError("审阅必须覆盖全部失败模式且不能遗漏")
    return decisions


class ReviewEngine:
    """默认最多 4 次模型调用；失败发现允许一次格式重问。"""

    def __init__(
        self,
        model: LanguageModel,
        *,
        max_findings: int = 5,
        reporter: ReviewConsoleReporter | None = None,
    ) -> None:
        if type(max_findings) is not int or not 1 <= max_findings <= MAX_FINDINGS:
            raise ValueError(f"max_findings 必须在 1..{MAX_FINDINGS}")
        self.model = model
        self.max_findings = max_findings
        self.reporter = reporter or ReviewConsoleReporter(quiet=True)

    async def run(self, task: str, context: dict[str, str] | None = None) -> ReviewResult:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("任务不能为空")
        task = task.strip()
        context = _clean_context(context)
        started_at = utc_now()
        self.reporter.run_started(task)
        calls = 0

        async def ask(system: str, user: str) -> str:
            nonlocal calls
            calls += 1
            answer = await self.model.complete(system, user)
            if not isinstance(answer, str) or not answer.strip():
                raise ModelResponseError("模型响应正文为空或不是文本")
            return answer

        draft_future = asyncio.ensure_future(
            ask(REVIEW_DRAFT, json.dumps({"task": task, "context": context}, ensure_ascii=False))
        )
        probe_future = asyncio.ensure_future(self._probe(ask, task, context))
        draft_raw, probe_raw = await asyncio.gather(draft_future, probe_future, return_exceptions=True)

        if isinstance(draft_raw, BaseException):
            if isinstance(draft_raw, asyncio.CancelledError):
                raise draft_raw
            return self._finish(task, "", [], "", "model_failed", calls, started_at)
        draft = draft_raw.strip() or ""
        self.reporter.draft_ready()

        if isinstance(probe_raw, BaseException):
            if isinstance(probe_raw, asyncio.CancelledError):
                raise probe_raw
            return self._finish(task, draft, [], draft, "probe_failed", calls, started_at)
        findings = probe_raw
        if not findings:
            return self._finish(task, draft, [], draft, "no_findings", calls, started_at)
        self.reporter.findings_ready(findings)

        try:
            decisions = await self._decide(ask, task, context, findings)
        except (ReviewFormatError, ModelError):
            return self._finish(task, draft, findings, draft, "review_failed", calls, started_at)
        for finding in findings:
            verdict, reason = decisions[finding.id]
            finding.status = _DECISION_TO_STATUS[verdict]
            finding.review_reason = reason

        supported = [finding for finding in findings if finding.status == "supported"]
        if not supported:
            return self._finish(task, draft, findings, draft, "draft_only", calls, started_at)

        try:
            final = await ask(
                REVIEW_FINAL,
                json.dumps(
                    {
                        "task": task,
                        "context": context,
                        "draft": draft,
                        "supported_findings": [finding.to_dict() for finding in supported],
                    },
                    ensure_ascii=False,
                ),
            )
        except (ModelError, JsonProtocolError):
            return self._finish(task, draft, findings, draft, "review_failed", calls, started_at)
        final = final.strip() or draft
        return self._finish(task, draft, findings, final, "completed", calls, started_at)

    async def _probe(self, ask: Ask, task: str, context: dict[str, str]) -> list[Finding]:
        prompt = REVIEW_PROBE.replace("__MAX_FINDINGS__", str(self.max_findings))
        payload = json.dumps(
            {"task": task, "context": context, "max_findings": self.max_findings},
            ensure_ascii=False,
        )
        corpus = _corpus(task, context)
        last_error: Exception | None = None
        for attempt in range(2):
            system = prompt if attempt == 0 else (
                prompt + "\n\n[格式重试] 上一次回复不符合要求。只输出一个完整的 JSON 对象，字段与示例一致。"
            )
            text = await ask(system, payload)
            try:
                return parse_findings(text, max_findings=self.max_findings, corpus=corpus)
            except (ReviewFormatError, JsonProtocolError) as error:
                last_error = error
        raise ModelResponseError(f"失败发现结构无效：{last_error}")

    async def _decide(
        self, ask: Ask, task: str, context: dict[str, str], findings: list[Finding],
    ) -> dict[str, tuple[str, str]]:
        payload = json.dumps(
            {"task": task, "context": context, "findings": [finding.to_dict() for finding in findings]},
            ensure_ascii=False,
        )
        text = await ask(REVIEW_DECISIONS, payload)
        try:
            return parse_decisions(text, findings)
        except (ReviewFormatError, JsonProtocolError) as error:
            raise ModelResponseError(f"审阅结构无效：{error}") from None

    def _finish(
        self,
        task: str,
        draft: str,
        findings: list[Finding],
        final_output: str,
        status: str,
        calls: int,
        started_at: str,
    ) -> ReviewResult:
        result = ReviewResult(
            task=task,
            draft=draft,
            findings=findings,
            final_output=final_output,
            status=status,
            model_calls=calls,
            started_at=started_at,
            finished_at=utc_now(),
        )
        self.reporter.run_completed(status)
        return result
