"""预算与故障恢复测试，全部使用本地模拟接口。"""
import asyncio
import json
import threading
import urllib.error

import pytest

from dualpath.budgeted import AccountingError, BudgetedDeepSeek, BudgetExceeded, Ledger, cost_units


def response():
    return {"usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_cache_hit_tokens": 50},
            "choices": [{"message": {"content": "pass"}, "finish_reason": "stop"}]}


def test_prices_reject_missing_usage():
    assert cost_units(response()["usage"]) == 182
    for bad in ({}, {"prompt_tokens": True, "completion_tokens": 0},
                {"prompt_tokens": 2, "completion_tokens": 1, "prompt_cache_hit_tokens": 3}):
        with pytest.raises(AccountingError):
            cost_units(bad)


@pytest.mark.asyncio
async def test_concurrent_calls_reserve_before_network(tmp_path):
    called = []
    started, release = threading.Event(), threading.Event()
    def transport(payload):
        called.append(payload)
        started.set()
        assert release.wait(3)
        return response()
    with Ledger(tmp_path / "ledger.json", .025) as ledger:
        model = BudgetedDeepSeek("secret-not-logged", ledger, transport=transport)
        first = asyncio.create_task(model.complete("a", "b"))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            with pytest.raises(BudgetExceeded):
                await model.complete("a", "b")
        finally:
            release.set()
        await first
        assert len(called) == 1
        assert ledger.spent_units == 182
    assert "secret-not-logged" not in (tmp_path / "ledger.json").read_text()


@pytest.mark.asyncio
async def test_error_preserves_reservation_and_does_not_log_exception(tmp_path):
    path = tmp_path / "ledger.json"
    def fail(payload):
        raise OSError("secret-not-logged")
    with Ledger(path, .025) as ledger:
        model = BudgetedDeepSeek("secret-not-logged", ledger, transport=fail)
        with pytest.raises(RuntimeError, match="OSError"):
            await model.complete("a", "b")
        reserved = ledger.spent_units
    with Ledger(path, .025) as ledger:
        assert ledger.spent_units == reserved
        with pytest.raises(BudgetExceeded):
            await BudgetedDeepSeek("key", ledger, transport=lambda _: response()).complete("a", "b")
    assert "secret-not-logged" not in path.read_text()


@pytest.mark.asyncio
async def test_unknown_usage_halts_future_calls(tmp_path):
    with Ledger(tmp_path / "ledger.json", 1) as ledger:
        model = BudgetedDeepSeek("key", ledger, transport=lambda _: {"usage": {}})
        with pytest.raises(RuntimeError):
            await model.complete("a", "b")
        assert ledger.data["halted"]
        with pytest.raises(AccountingError):
            await model.complete("a", "b")


@pytest.mark.asyncio
async def test_pending_record_and_cross_process_lock(tmp_path):
    path = tmp_path / "ledger.json"
    with Ledger(path, 1) as ledger:
        ledger.reserve({"messages": [], "max_tokens": 2048}, "x", 1_000_000)
        with pytest.raises(BlockingIOError):
            Ledger(path, 1)
        charged = ledger.spent_units
    with Ledger(path, 1) as ledger:
        assert ledger.spent_units == charged and ledger.data["calls"][0]["status"] == "pending"
    with pytest.raises(ValueError):
        Ledger(path, 2)


@pytest.mark.asyncio
async def test_group_limit_applies_independently(tmp_path):
    with Ledger(tmp_path / "ledger.json", 1) as ledger:
        with pytest.raises(BudgetExceeded):
            await BudgetedDeepSeek("key", ledger, group_limit_cny=.001, transport=lambda _: response()).complete("a", "b")
        assert not ledger.data["calls"]


@pytest.mark.asyncio
async def test_http_status_is_logged_without_response_or_headers(tmp_path):
    def fail(payload):
        raise urllib.error.HTTPError("https://example.invalid/private", 401, "secret", {"Authorization": "secret"}, None)
    path = tmp_path / "ledger.json"
    with Ledger(path, 1) as ledger:
        with pytest.raises(RuntimeError):
            await BudgetedDeepSeek("secret", ledger, transport=fail).complete("a","b")
        assert ledger.data["calls"][0]["http_status"] == 401
    assert "secret" not in path.read_text() and "private" not in path.read_text()
