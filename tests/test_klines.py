"""近期高点（止损基准）的测试。**不出网**：HTTP 一律打桩。

关键约定：只用**已收盘**的 bar；拿不到就说拿不到（None），不用别的价冒充。
"""

from __future__ import annotations

import pytest

from agent.portfolio.klines import (
    bars_needed,
    fetch_recent_highs,
    high_of,
    interval_ms,
    parse_high_response,
    parse_klines,
    pick_interval,
)

NOW_MS = 1_790_000_000_000


def _bar(open_time: int, high: float, *, close_time: int | None = None, low: float = 1.0):
    return [
        open_time,
        "1.0",
        str(high),
        str(low),
        "1.0",
        "10.0",
        close_time if close_time is not None else open_time + 60_000 - 1,
    ]


# ---------------------------------------------------------------- 粒度算术


def test_interval_ms_known_units():
    assert interval_ms("1m") == 60_000
    assert interval_ms("15m") == 900_000
    assert interval_ms("1h") == 3_600_000
    assert interval_ms("4h") == 14_400_000
    assert interval_ms("1d") == 86_400_000


def test_interval_ms_refuses_unknown():
    assert interval_ms("1x") is None
    assert interval_ms("hour") is None
    assert interval_ms("") is None
    assert interval_ms("m") is None


def test_bars_needed_covers_the_window():
    # 24 小时 1m 线 = 1440 根，多要一根防边界
    assert bars_needed(24, "1m") == 1441
    assert bars_needed(24, "1h") == 25
    assert bars_needed(24, "1x") is None


def test_pick_interval_stays_under_the_api_cap():
    """回看越久，粒度就得越粗 —— 否则一次拉不回那么多根。

    默认窗口 24 小时在 1m 下要 1441 根，超过币安单次 1000 的上限，
    所以退到 5m（289 根）。这不是降级，是不让窗口被静默截短。
    """
    assert pick_interval(1) == "1m"          # 61 根
    assert pick_interval(24) == "5m"         # 1m 要 1441 根，装不下
    assert pick_interval(24 * 30) == "1h"    # 5m 要 8641 根，也装不下
    assert bars_needed(24, "5m") <= 1000


def test_pick_interval_returns_none_when_nothing_fits():
    assert pick_interval(10**9) is None


# ---------------------------------------------------------------- 解析


def test_unclosed_bar_is_dropped():
    """**关键**：未收盘那根的高点还在动，当历史高点用会让止损值反复变。"""
    payload = [
        _bar(NOW_MS - 120_000, high=100.0),
        _bar(NOW_MS - 60_000, high=999.0, close_time=NOW_MS + 30_000),  # 还在长
    ]
    bars = parse_klines(payload, now_ms=NOW_MS)
    assert len(bars) == 1
    assert high_of(bars) == 100.0


def test_high_is_the_max_of_closed_bars():
    payload = [
        _bar(NOW_MS - 180_000, high=100.0),
        _bar(NOW_MS - 120_000, high=250.0),
        _bar(NOW_MS - 60_000, high=150.0),
    ]
    assert parse_high_response(payload, now_ms=NOW_MS) == 250.0


def test_no_bars_means_none_not_zero():
    """拿不到就是 None。返回 0 会让"回落 100%"这种鬼话成立。"""
    assert parse_high_response([], now_ms=NOW_MS) is None
    assert high_of([]) is None


def test_malformed_rows_are_skipped_not_fatal():
    payload = [
        "garbage",
        [1, 2, 3],  # 太短
        _bar(NOW_MS - 60_000, high=50.0, close_time=NOW_MS - 1),
    ]
    assert parse_high_response(payload, now_ms=NOW_MS) == 50.0


def test_non_list_payload_is_none():
    assert parse_high_response({"nope": 1}, now_ms=NOW_MS) is None


# ---------------------------------------------------------------- 取数


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, by_symbol, *, fail=()):
        self.by_symbol = by_symbol
        self.fail = set(fail)
        self.calls = []

    async def get(self, path, params=None):
        symbol = params["symbol"]
        self.calls.append((path, params))
        if symbol in self.fail:
            raise RuntimeError("boom")
        return FakeResponse(self.by_symbol.get(symbol, []))


@pytest.mark.asyncio
async def test_fetch_recent_highs_maps_assets_to_symbols():
    from agent.config import Settings

    client = FakeClient(
        {"BTCUSDT": [_bar(NOW_MS - 60_000, high=83083.12, close_time=NOW_MS - 1)]}
    )
    out = await fetch_recent_highs(
        ["btc"], settings=Settings(), client=client, now_ms=NOW_MS, lookback_hours=24
    )
    assert out == {"BTC": 83083.12}
    assert client.calls[0][1]["interval"]  # 自动挑了粒度


@pytest.mark.asyncio
async def test_one_failed_asset_does_not_sink_the_others():
    from agent.config import Settings

    client = FakeClient(
        {
            "BTCUSDT": [_bar(NOW_MS - 60_000, high=100.0, close_time=NOW_MS - 1)],
            "ETHUSDT": [_bar(NOW_MS - 60_000, high=200.0, close_time=NOW_MS - 1)],
        },
        fail={"ETHUSDT"},
    )
    out = await fetch_recent_highs(
        ["BTC", "ETH"], settings=Settings(), client=client, now_ms=NOW_MS, lookback_hours=24
    )
    assert out["BTC"] == 100.0
    assert out["ETH"] is None  # 缺席而不是编一个价


@pytest.mark.asyncio
async def test_empty_asset_list_makes_no_calls():
    from agent.config import Settings

    client = FakeClient({})
    assert await fetch_recent_highs([], settings=Settings(), client=client) == {}
    assert client.calls == []
