"""有持久化预算账本的 DeepSeek 接口；不自动重试网络请求。

价格是 2026-10-31 官方 deepseek-flash 高峰价，单位为元/百万 token。
这是本地保守估算上限；供应商改价后须更新价格，不能代替账户账单限额。
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from .verification import Completion


class BudgetExceeded(RuntimeError):
    pass


class AccountingError(RuntimeError):
    pass


def units(cny):
    value = Decimal(str(cny))
    if not value.is_finite() or value <= 0:
        raise ValueError("费用上限必须为有限正数")
    return int((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def cost_units(usage):
    """向上取整至微元；缓存输入使用高峰价 0.04 元/百万 token。"""
    p, c, h = (usage.get(k, 0 if k == "prompt_cache_hit_tokens" else None)
               for k in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens"))
    if any(type(v) is not int or v < 0 for v in (p, c, h)) or h > p:
        raise AccountingError("响应缺少有效的 token 用量")
    return (p - h) * 2 + c * 8 + (h * 4 + 99) // 100


class Ledger:
    """整个运行持有排他锁；未确认的请求在重启后仍占用全部预留额。"""

    def __init__(self, path: str | Path, limit_cny: float):
        self.path = Path(path)
        self.limit = units(limit_cny)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix(self.path.suffix + ".lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.data = json.loads(self.path.read_text()) if self.path.exists() else {
                "version": 1, "model": "deepseek-flash", "price_date": "2026-10-31",
                "limit_units": self.limit, "halted": False, "calls": [],
            }
            if self.data.get("version") != 1 or self.data.get("limit_units") != self.limit:
                raise ValueError("旧账本格式或预算与本次不一致")
            self.save()
        except BaseException:
            self.lock.close()
            raise

    @property
    def spent_units(self):
        return sum(r.get("charged_units", r["reserved_units"]) for r in self.data["calls"])

    def group_units(self, group):
        return sum(r.get("charged_units", r["reserved_units"]) for r in self.data["calls"] if r["group"] == group)

    def save(self):
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        # 不写密钥；原始任务和模型输出仍可能是私密材料。
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(self.data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def reserve(self, request, group, group_limit):
        if self.data["halted"]:
            raise AccountingError("账本已因记账异常停止，须人工检查")
        bound = 2 * len(json.dumps(request["messages"], ensure_ascii=False).encode()) + 2048
        if bound > 150_000:
            raise ValueError("输入超过预算保护范围")
        amount = bound * 2 + request["max_tokens"] * 8
        if self.spent_units + amount > self.limit or self.group_units(group) + amount > group_limit:
            raise BudgetExceeded("已达到保守费用上限")
        row = {"number": len(self.data["calls"]) + 1, "group": group,
               "started_at": datetime.now(timezone.utc).isoformat(), "status": "pending",
               "request": request, "reserved_units": amount}
        self.data["calls"].append(row)
        self.save()
        return row

    def close(self):
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class BudgetedDeepSeek:
    """只读取调用方传入的密钥，保存完整请求/响应但不保存请求头。"""

    def __init__(self, api_key: str, ledger: Ledger, *, group="verify", group_limit_cny=None,
                 max_tokens=2048, transport=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("缺少 DEEPSEEK_API_KEY")
        if type(max_tokens) is not int or not 64 <= max_tokens <= 8192:
            raise ValueError("max_tokens 必须在 64..8192")
        self._key, self.ledger, self.group = api_key, ledger, group
        self.group_limit = units(group_limit_cny) if group_limit_cny is not None else ledger.limit
        self.max_tokens = max_tokens
        self._transport = transport or self._request

    def _request(self, payload):
        request = urllib.request.Request("https://api.deepseek.com/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        # urllib 不自动重试；连接错误也保留预算预留额。
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError("响应过大")
            return json.loads(raw)

    async def complete(self, system: str, user: str) -> Completion:
        payload = {"model": "deepseek-flash", "messages": [{"role": "system", "content": system},
            {"role": "user", "content": user}], "temperature": 0.2,
            "thinking": {"type": "disabled"}, "max_tokens": self.max_tokens, "stream": False}
        row = self.ledger.reserve(payload, self.group, self.group_limit)
        started = time.monotonic()
        try:
            response = await asyncio.to_thread(self._transport, payload)
            row["response"] = response
            cost = cost_units(response.get("usage", {}))
            row["charged_units"] = cost
            if cost > row["reserved_units"]:
                raise AccountingError("供应商用量超过预留额；停止后续调用")
            choice = response["choices"][0]
            text, reason = choice["message"]["content"], choice["finish_reason"]
            if not isinstance(text, str) or not isinstance(reason, str):
                raise ValueError("响应正文或结束原因无效")
            row["status"] = "ok"
            return Completion(text, reason)
        except BaseException as error:
            row["status"] = "error"
            row["error_type"] = type(error).__name__
            if isinstance(error, urllib.error.HTTPError):
                row["http_status"] = error.code
            if isinstance(error, AccountingError):
                self.ledger.data["halted"] = True
            if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            # 原始异常可能包含敏感连接信息，禁止写入日志或向上冒泡。
            raise RuntimeError("模型请求失败：" + type(error).__name__) from None
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.ledger.save()
