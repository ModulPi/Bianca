"""订单状态机与对账的纯函数测试。全程不出网、不碰数据库。

重点钉住两类东西：**幂等键必须是确定性的**（随机键等于没有幂等），
以及**拿不到可信回执时绝不记成成交**（旧实现 `filled or amount` 的谎报入口）。
"""

from __future__ import annotations

import re

import ccxt
import pytest

from agent.trading.orders import (
    CANCELED,
    FILLED,
    PARTIAL,
    REJECTED,
    SUBMITTED,
    UNKNOWN,
    OrderUpdate,
    _exhausted,
    classify_submit_error,
    is_terminal,
    new_client_order_id,
    normalize_order,
    reconcile,
    resolve_status,
)

# 币安 newClientOrderId 的合法字符集与长度上限
_BINANCE_CID_RE = re.compile(r"^[.A-Z:/a-z0-9_-]{1,36}$")


# ---------------------------------------------------------------- 幂等键


def test_client_order_id_is_deterministic():
    """同一决策重放必须得到同一个 id —— 否则交易所认不出是同一笔单。"""
    assert new_client_order_id("trade-abc") == new_client_order_id("trade-abc")


def test_client_order_id_differs_across_decisions():
    assert new_client_order_id("trade-abc") != new_client_order_id("trade-def")


def test_client_order_id_is_valid_for_binance():
    cid = new_client_order_id("a" * 200)  # 超长 seed 也不能撑破长度上限
    assert _BINANCE_CID_RE.match(cid), cid
    assert len(cid) <= 36


# ---------------------------------------------------------------- 归一化


def test_normalize_full_fill():
    update = normalize_order(
        {
            "id": 12345,
            "status": "closed",
            "filled": 0.001,
            "average": 65000.0,
            "cost": 65.0,
            "fee": {"cost": 0.05, "currency": "USDT"},
        }
    )
    assert update.status == FILLED
    assert update.exchange_order_id == "12345"
    assert update.filled_qty == 0.001
    assert update.avg_price == 65000.0
    assert update.cost_quote == 65.0
    assert "0.05" in update.fee


def test_closed_without_filled_is_not_reported_as_filled():
    """**回归**：交易所说 closed 却一个成交量都没给时，不许记 filled。

    旧实现 `order.get("filled") or amount` 正是在这里把请求量当成成交量。
    """
    update = normalize_order({"id": 1, "status": "closed", "filled": None})
    assert update.status == SUBMITTED
    assert update.filled_qty is None


def test_closed_with_zero_filled_is_not_reported_as_filled():
    update = normalize_order({"id": 1, "status": "closed", "filled": 0.0})
    assert update.status == SUBMITTED


def test_open_status_maps_to_submitted():
    update = normalize_order({"id": 1, "status": "open", "filled": 0.0005})
    assert update.status == SUBMITTED
    assert update.filled_qty == 0.0005


def test_unknown_exchange_status_does_not_become_terminal():
    """交易所给了没见过的状态 → 当作还在进行中，不能猜成终态。"""
    assert normalize_order({"id": 1, "status": "weird"}).status == SUBMITTED


def test_normalize_missing_average_falls_back_to_price():
    update = normalize_order({"id": 1, "status": "closed", "filled": 1.0, "price": 10.0})
    assert update.avg_price == 10.0


def test_normalize_never_invents_numbers():
    update = normalize_order({"status": "open"})
    assert update.exchange_order_id is None
    assert update.filled_qty is None
    assert update.avg_price is None
    assert update.cost_quote is None


def test_resolve_status_keeps_filled_when_evidence_present():
    assert resolve_status(FILLED, 0.5) == FILLED
    assert resolve_status(CANCELED, 0.0) == CANCELED


# ---------------------------------------------------------------- 提交失败的性质


def test_network_error_is_unknown_not_rejected():
    """**关键**：超时下交易所可能已经收单了，记 rejected 会诱导重放 → 重复下单。"""
    assert classify_submit_error(ccxt.RequestTimeout("timeout")) == UNKNOWN


def test_connection_error_is_unknown():
    assert classify_submit_error(ccxt.ExchangeNotAvailable("down")) == UNKNOWN


def test_exchange_rejection_is_rejected():
    assert classify_submit_error(ccxt.InsufficientFunds("no funds")) == REJECTED


def test_unclassifiable_error_defaults_to_unknown():
    """不认识就当成"可能已经下出去了" —— 保守方向是少下单，不是多下单。"""
    assert classify_submit_error(RuntimeError("?")) == UNKNOWN


# ---------------------------------------------------------------- 终态集合


def test_unknown_is_terminal():
    """unknown 必须是终态：它阻断重放，等人处理。"""
    assert is_terminal(UNKNOWN)


def test_terminal_statuses():
    for status in (FILLED, PARTIAL, CANCELED, REJECTED, UNKNOWN):
        assert is_terminal(status)
    assert not is_terminal(SUBMITTED)
    assert not is_terminal("pending")


# ---------------------------------------------------------------- 对账


async def _noop_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_reconcile_returns_immediately_on_terminal():
    calls = []

    async def fetch(order_id: str) -> dict:
        calls.append(order_id)
        return {"id": order_id, "status": "closed", "filled": 0.002, "average": 65000.0}

    update = await reconcile(fetch, exchange_order_id="42", sleep=_noop_sleep)
    assert update.status == FILLED
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_reconcile_polls_until_terminal():
    responses = [
        {"id": "42", "status": "open", "filled": 0.0},
        {"id": "42", "status": "open", "filled": 0.0},
        {"id": "42", "status": "closed", "filled": 0.002, "average": 65000.0},
    ]

    async def fetch(order_id: str) -> dict:
        return responses.pop(0)

    update = await reconcile(fetch, exchange_order_id="42", attempts=5, sleep=_noop_sleep)
    assert update.status == FILLED
    assert update.filled_qty == 0.002
    assert responses == []


@pytest.mark.asyncio
async def test_reconcile_exhausted_with_partial_fill():
    async def fetch(order_id: str) -> dict:
        return {"id": order_id, "status": "open", "filled": 0.001}

    update = await reconcile(fetch, exchange_order_id="42", attempts=3, sleep=_noop_sleep)
    assert update.status == PARTIAL
    assert update.filled_qty == 0.001


@pytest.mark.asyncio
async def test_reconcile_exhausted_without_fill_is_unknown():
    async def fetch(order_id: str) -> dict:
        return {"id": order_id, "status": "open", "filled": 0.0}

    update = await reconcile(fetch, exchange_order_id="42", attempts=3, sleep=_noop_sleep)
    assert update.status == UNKNOWN


@pytest.mark.asyncio
async def test_reconcile_never_raises_on_fetch_failure():
    """对账自己失败不能带崩 tick；问不出来就是问不出来。"""

    async def fetch(order_id: str) -> dict:
        raise RuntimeError("network hiccup")

    update = await reconcile(fetch, exchange_order_id="42", attempts=3, sleep=_noop_sleep)
    assert update.status == UNKNOWN


@pytest.mark.asyncio
async def test_reconcile_survives_failure_then_recovers():
    state = {"n": 0}

    async def fetch(order_id: str) -> dict:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("transient")
        return {"id": order_id, "status": "closed", "filled": 0.003, "average": 65000.0}

    update = await reconcile(fetch, exchange_order_id="42", attempts=4, sleep=_noop_sleep)
    assert update.status == FILLED


def test_exhausted_preserves_known_fields():
    last = OrderUpdate(status=SUBMITTED, exchange_order_id="42", filled_qty=0.001, avg_price=100.0)
    result = _exhausted(last)
    assert result.status == PARTIAL
    assert result.avg_price == 100.0
    assert _exhausted(None).status == UNKNOWN
