"""持仓、权益、日盈亏的测试。**全程不出网**（交易所一律打桩）。

三条主线：
  · **取不到价不许当 0** —— 那会让权益悄悄地偏低；
  · **只有落定的成交才归因** —— `unknown` 可能成交了也可能没有，按成交算就是编数；
  · **算不出日盈亏就说不知道** —— 不返回 0.0，否则熔断会静默失效。
数据库部分是临时 sqlite（`db` fixture），不是真库。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from agent.portfolio.attribution import (
    HoldingGap,
    attributed_holdings,
    compare,
    split_symbol,
)
from agent.portfolio.service import build_view, price_assets
from agent.portfolio.valuation import CASH_ASSETS, value_portfolio

# ---------------------------------------------------------------- symbol 切分


def test_split_symbol_basic():
    assert split_symbol("BTCUSDT") == ("BTC", "USDT")
    assert split_symbol("ETHUSDC") == ("ETH", "USDC")
    assert split_symbol("BTCUSD") == ("BTC", "USD")
    assert split_symbol("ethusdt") == ("ETH", "USDT")  # 大小写不敏感


def test_split_symbol_crypto_quote():
    assert split_symbol("ETHBTC") == ("ETH", "BTC")


def test_split_symbol_refuses_to_guess():
    # 切不出来就返回 None，不能瞎切。`USDT` 本身不是交易对。
    assert split_symbol("USDT") is None
    assert split_symbol("WEIRDPAIR") is None
    assert split_symbol("") is None


# ---------------------------------------------------------------- 归因


@dataclass
class FakeOrder:
    symbol: str = "BTCUSDT"
    side: str = "buy"
    status: str = "filled"
    filled_qty: float | None = 0.001
    cost_quote: float | None = 65.0


def test_buy_moves_base_up_and_quote_down():
    holdings, unmapped = attributed_holdings([FakeOrder()])
    assert holdings["BTC"] == pytest.approx(0.001)
    assert holdings["USDT"] == pytest.approx(-65.0)
    assert unmapped == []


def test_sell_moves_base_down_and_quote_up():
    holdings, _ = attributed_holdings([FakeOrder(side="sell")])
    assert holdings["BTC"] == pytest.approx(-0.001)
    assert holdings["USDT"] == pytest.approx(65.0)


def test_unknown_status_is_not_attributed():
    """**关键**：`unknown` 可能已经成交了，按成交计入等于编数。

    不计入会让账外差异里多出这一笔 —— 那正是我们希望被人看见的。
    """
    holdings, _ = attributed_holdings([FakeOrder(status="unknown")])
    assert holdings == {}


def test_rejected_and_canceled_are_not_attributed():
    for status in ("rejected", "canceled", "pending", "submitted"):
        holdings, _ = attributed_holdings([FakeOrder(status=status)])
        assert holdings == {}, status


def test_partial_is_attributed():
    holdings, _ = attributed_holdings([FakeOrder(status="partial")])
    assert holdings["BTC"] == pytest.approx(0.001)


def test_zero_fill_contributes_nothing():
    holdings, _ = attributed_holdings([FakeOrder(filled_qty=0.0)])
    assert holdings == {}


def test_unmappable_symbol_is_reported_not_guessed():
    holdings, unmapped = attributed_holdings([FakeOrder(symbol="WEIRDPAIR")])
    assert holdings == {}
    assert unmapped == ["WEIRDPAIR"]


def test_missing_cost_quote_leaves_quote_unattributed():
    """花掉多少不知道就不记 —— 可见的偏差好过不可见的编造。"""
    holdings, _ = attributed_holdings([FakeOrder(cost_quote=None)])
    assert "BTC" in holdings
    assert "USDT" not in holdings


def test_attribution_accumulates_across_orders():
    holdings, _ = attributed_holdings(
        [
            FakeOrder(filled_qty=0.001, cost_quote=65.0),
            FakeOrder(filled_qty=0.002, cost_quote=130.0),
            FakeOrder(side="sell", filled_qty=0.0005, cost_quote=33.0),
        ]
    )
    assert holdings["BTC"] == pytest.approx(0.0025)
    assert holdings["USDT"] == pytest.approx(-162.0)


# ---------------------------------------------------------------- 对比


def test_compare_includes_assets_present_on_only_one_side():
    gaps = {g.asset: g for g in compare({"BTC": 0.005}, {"BTC": 0.001, "ETH": 1.0})}
    assert set(gaps) == {"BTC", "ETH"}
    assert gaps["BTC"].unexplained_qty == pytest.approx(0.004)
    assert not gaps["BTC"].explained
    # 账本里记着、账户里没有 —— 也必须是可见的（负数）
    assert gaps["ETH"].unexplained_qty == pytest.approx(-1.0)


def test_compare_marks_explained_when_equal():
    gap = compare({"BTC": 0.001}, {"BTC": 0.001})[0]
    assert gap.explained


# ---------------------------------------------------------------- 估值


def test_cash_assets_counted_at_face():
    v = value_portfolio({"USDT": 1000.0, "USDC": 500.0}, {})
    assert v.cash_quote == pytest.approx(1500.0)
    assert v.holdings_value == 0.0
    assert v.equity == pytest.approx(1500.0)


def test_holdings_valued_at_given_price():
    v = value_portfolio({"USDT": 1000.0, "BTC": 0.01}, {"BTC": 65000.0})
    assert v.holdings_value == pytest.approx(650.0)
    assert v.equity == pytest.approx(1650.0)


def test_unpriced_holding_is_excluded_and_flagged():
    """**关键**：取不到价时按 0 计会悄悄低估权益。必须排除并标记。"""
    v = value_portfolio({"USDT": 1000.0, "XYZ": 10.0}, {})
    assert v.unpriced == ("XYZ",)
    assert v.equity == pytest.approx(1000.0)  # 只有现金
    assert v.notes  # 必须留下痕迹
    assert "下界" in " ".join(v.notes)


def test_zero_price_is_treated_as_unpriced():
    v = value_portfolio({"XYZ": 10.0}, {"XYZ": 0.0})
    assert v.unpriced == ("XYZ",)
    assert v.equity == 0.0


def test_negative_price_is_treated_as_unpriced():
    assert value_portfolio({"XYZ": 1.0}, {"XYZ": -5.0}).unpriced == ("XYZ",)


def test_dust_is_ignored():
    v = value_portfolio({"BTC": 1e-12}, {"BTC": 65000.0})
    assert v.positions == ()
    assert v.unpriced == ()


def test_zero_balance_ignored():
    v = value_portfolio({"BTC": 0.0, "USDT": 10.0}, {"BTC": 65000.0})
    assert [p.asset for p in v.positions] == ["USDT"]


def test_position_records_are_frozen_and_priced_flag():
    v = value_portfolio({"BTC": 0.01, "XYZ": 1.0}, {"BTC": 65000.0})
    by_asset = {p.asset: p for p in v.positions}
    assert by_asset["BTC"].priced
    assert not by_asset["XYZ"].priced
    assert by_asset["XYZ"].value_quote is None


# ---------------------------------------------------------------- 取价目标


def test_price_assets_skips_cash_and_empty():
    assert price_assets({"USDT": 100.0, "BTC": 0.1, "ETH": 0.0}) == ["BTC"]


def test_cash_assets_are_all_upper():
    assert all(a == a.upper() for a in CASH_ASSETS)


# ---------------------------------------------------------------- 合成


def test_build_view_composes_all_three_sources():
    view = build_view(
        balances={"USDT": 1000.0, "BTC": 0.01},
        prices={"BTC": 65000.0},
        orders=[FakeOrder(filled_qty=0.001, cost_quote=65.0)],
    )
    assert view.valuation.equity == pytest.approx(1650.0)
    gaps = {g.asset: g for g in view.gaps}
    # 交易所 0.01，账本只解释得了 0.001 —— 差的那 0.009 必须是可见的
    assert gaps["BTC"].unexplained_qty == pytest.approx(0.009)
    assert {g.asset for g in view.unexplained} == {"BTC", "USDT"}


def test_build_view_reports_unmapped_symbols():
    view = build_view({}, {}, [FakeOrder(symbol="WEIRDPAIR")])
    assert view.unmapped_symbols == ("WEIRDPAIR",)


def test_view_as_dict_is_json_ready():
    import json

    view = build_view({"USDT": 10.0, "BTC": 0.01}, {"BTC": 65000.0}, [FakeOrder()])
    assert json.loads(json.dumps(view.as_dict()))["equity"] == pytest.approx(660.0)


def test_holding_gap_explained_boundary():
    assert HoldingGap(asset="BTC", exchange_qty=1.0, attributed_qty=1.0).explained
    assert not HoldingGap(asset="BTC", exchange_qty=1.0, attributed_qty=0.9).explained


# ---------------------------------------------------------------- API（离线）


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from agent.config import Settings
    from agent.storage import database

    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


@pytest.mark.asyncio
async def test_snapshot_endpoint_persists_and_get_reads_it_back(db, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from agent.main import app
    from agent.portfolio import service

    async def fake_balance(_settings):
        return {"USDT": 1000.0, "BTC": 0.01}

    async def fake_prices(assets, *, settings, **kwargs):
        return {a: 65000.0 for a in assets}

    monkeypatch.setattr(service, "_fetch_balance", fake_balance)
    monkeypatch.setattr(service, "fetch_prices", fake_prices)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/v1/portfolio")
        assert before.status_code == 404  # 没有快照就 404，不编一个 0

        resp = await client.post("/api/v1/portfolio/snapshot")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["equity"] == pytest.approx(1650.0)
        assert body["cash_quote"] == pytest.approx(1000.0)

        after = await client.get("/api/v1/portfolio")
        assert after.status_code == 200
        assert after.json()["equity"] == pytest.approx(1650.0)
        assert after.json()["captured_at"] == body["captured_at"]
        # 两条路必须说同一套话 —— 返回的视图要和落库的内容一致
        assert after.json()["notes"] == body["notes"]
        assert after.json()["gaps"] == body["gaps"]


@pytest.mark.asyncio
async def test_snapshot_endpoint_reports_unpriced_holdings(db, monkeypatch):
    """取不到价的持仓必须出现在响应里 —— 否则权益看着像个完整值。"""
    from httpx import ASGITransport, AsyncClient

    from agent.main import app
    from agent.portfolio import service

    async def fake_balance(_settings):
        return {"USDT": 100.0, "XYZ": 5.0}

    async def no_prices(assets, *, settings, **kwargs):
        return {a: None for a in assets}

    monkeypatch.setattr(service, "_fetch_balance", fake_balance)
    monkeypatch.setattr(service, "fetch_prices", no_prices)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.post("/api/v1/portfolio/snapshot")).json()

    assert body["unpriced"] == ["XYZ"]
    assert body["equity"] == pytest.approx(100.0)
    assert any("下界" in n for n in body["notes"])


# ---------------------------------------------------------------- 账外漂移


def test_drift_is_empty_when_nothing_changed():
    from agent.portfolio.attribution import unexplained_drift

    gaps = compare({"BTC": 0.01}, {"BTC": 0.001})
    assert unexplained_drift({"BTC": 0.009}, gaps) == []


def test_drift_reports_the_change_not_the_level():
    """**关键**：裸的账外差额永远是满格的，只有它的变化才说明出事了。"""
    from agent.portfolio.attribution import unexplained_drift

    gaps = compare({"BTC": 0.02}, {"BTC": 0.001})  # 账外 0.019
    assert unexplained_drift({"BTC": 0.009}, gaps) == [("BTC", pytest.approx(0.01))]


def test_drift_covers_assets_absent_from_previous():
    from agent.portfolio.attribution import unexplained_drift

    gaps = compare({"ETH": 1.0}, {})
    assert unexplained_drift({}, gaps) == [("ETH", pytest.approx(1.0))]


def test_drift_ignores_sub_tolerance_noise():
    from agent.portfolio.attribution import unexplained_drift

    gaps = compare({"BTC": 0.001000000001}, {"BTC": 0.001})
    assert unexplained_drift({"BTC": 0.0}, gaps) == []


@pytest.mark.asyncio
async def test_capture_notes_first_snapshot_as_baseline(db, monkeypatch):
    from agent.portfolio import service

    async def fake_balance(_settings):
        return {"USDT": 100.0, "BTC": 0.01}

    async def fake_prices(assets, *, settings, **kwargs):
        return {a: 1000.0 for a in assets}

    monkeypatch.setattr(service, "_fetch_balance", fake_balance)
    monkeypatch.setattr(service, "fetch_prices", fake_prices)

    first = await service.capture()
    assert any("首次快照" in n for n in first.valuation.notes) or any(
        "期初" in n for n in first.valuation.notes
    )


@pytest.mark.asyncio
async def test_balance_fetch_retries_transient_timeout(monkeypatch):
    """实测过 `load_markets()` 会偶发 RequestTimeout —— 一次失败不该让快照全空。"""
    from agent.config import Settings
    from agent.portfolio import service

    calls = {"n": 0}

    class Flaky:
        def __init__(self, _settings=None):
            pass

        async def __aenter__(self):
            calls["n"] += 1
            if calls["n"] < 3:
                raise __import__("ccxt").RequestTimeout("transient")
            return self

        async def __aexit__(self, *args):
            return None

        async def fetch_balance(self):
            return {"total": {"USDT": 42.0}}

    monkeypatch.setattr("agent.exchange.spot_demo.SpotDemoExchange", Flaky)

    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    result = await service._fetch_balance(Settings(), sleep=fake_sleep)
    assert result == {"USDT": 42.0}
    assert calls["n"] == 3
    assert slept == [1.5, 3.0]  # 指数退避


@pytest.mark.asyncio
async def test_balance_fetch_gives_up_after_attempts(monkeypatch):
    import ccxt

    from agent.config import Settings
    from agent.portfolio import service

    class AlwaysDown:
        def __init__(self, _settings=None):
            pass

        async def __aenter__(self):
            raise ccxt.RequestTimeout("down")

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr("agent.exchange.spot_demo.SpotDemoExchange", AlwaysDown)

    async def fake_sleep(_s):
        return None

    with pytest.raises(ccxt.RequestTimeout):
        await service._fetch_balance(Settings(), attempts=2, sleep=fake_sleep)


# ---------------------------------------------------------------- 日盈亏（权益口径）


async def _snap(equity: float, captured_at: str) -> None:
    from agent.storage.repository import EquityRepository

    await EquityRepository().save(
        cash_quote=equity,
        holdings_value=0.0,
        equity=equity,
        positions=[],
        unpriced=[],
        gaps=[],
        notes=[],
        captured_at=captured_at,
    )


@pytest.mark.asyncio
async def test_no_snapshot_today_means_unknown_not_zero(db):
    """**关键**：今天没有基准就说不知道，绝不返回 0.0。

    0.0 会被日亏熔断读成"今天没亏"，于是熔断在当天第一笔单上恰好是哑的。
    """
    from agent.portfolio.pnl import daily_pnl

    # 昨天有快照，今天没有
    await _snap(10000.0, "2026-10-10T12:00:00+00:00")

    now = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)
    result = await daily_pnl(now=now)

    assert result.known is False
    assert result.pnl is None
    assert "没有权益快照" in result.reason


@pytest.mark.asyncio
async def test_pnl_is_current_minus_first_snapshot_of_the_day(db):
    """基准是**当天第一条**，不是最近一条 —— 否则盈亏会被越算越小。"""
    from agent.portfolio.pnl import daily_pnl

    await _snap(10000.0, "2026-10-10T23:59:00+00:00")  # 昨天，不能当基准
    await _snap(9900.0, "2026-10-11T00:00:30+00:00")  # 今天第一条 → 基准
    await _snap(9850.0, "2026-10-11T06:00:00+00:00")  # 当前

    now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    result = await daily_pnl(now=now)

    assert result.known is True
    assert result.baseline_equity == pytest.approx(9900.0)
    assert result.current_equity == pytest.approx(9850.0)
    assert result.pnl == pytest.approx(-50.0)


@pytest.mark.asyncio
async def test_unrealized_loss_shows_up_in_pnl(db):
    """没有任何成交，权益只因为持仓跌了就下来了 —— 盈亏照样反映出来。"""
    from agent.portfolio.pnl import daily_pnl

    await _snap(10000.0, "2026-10-11T00:00:10+00:00")
    await _snap(9800.0, "2026-10-11T08:00:00+00:00")

    result = await daily_pnl(now=datetime(2026, 10, 11, 9, 0, tzinfo=UTC))

    assert result.pnl == pytest.approx(-200.0)


@pytest.mark.asyncio
async def test_single_snapshot_today_is_zero_not_unknown(db):
    """只有一条：它既是基准又是当前值，盈亏 0 —— 这是**已知的** 0，不是猜的。"""
    from agent.portfolio.pnl import daily_pnl

    await _snap(10000.0, "2026-10-11T00:00:10+00:00")

    result = await daily_pnl(now=datetime(2026, 10, 11, 1, 0, tzinfo=UTC))

    assert result.known is True
    assert result.pnl == pytest.approx(0.0)


def test_day_start_is_utc_midnight():
    from agent.portfolio.pnl import utc_day_start

    moment = datetime(2026, 10, 11, 23, 30, 5, 123456, tzinfo=UTC)
    assert utc_day_start(moment) == "2026-10-11T00:00:00+00:00"


def test_day_start_normalises_other_offsets_to_utc():
    """带 +08:00 的时刻要先换算再切日 —— 否则"今天"会比 UTC 早 8 小时开始。"""
    from datetime import timedelta, timezone

    from agent.portfolio.pnl import utc_day_start

    moment = datetime(2026, 10, 11, 3, 0, tzinfo=timezone(timedelta(hours=8)))
    # 03:00+08:00 = 前一天 19:00 UTC
    assert utc_day_start(moment) == "2026-10-10T00:00:00+00:00"


# ---------------------------------------------------------------- 回撤（ADR-029）


@pytest.mark.asyncio
async def test_no_snapshot_means_drawdown_unknown(db):
    from agent.portfolio.pnl import drawdown

    result = await drawdown()

    assert result.known is False
    assert result.pct is None
    assert "没有权益快照" in result.reason


@pytest.mark.asyncio
async def test_drawdown_is_measured_from_the_peak_not_the_first(db):
    """**关键**：基准是**最高**那张，不是第一张 —— 否则"回撤"答的是另一个问题。"""
    from agent.portfolio.pnl import drawdown

    await _snap(10000.0, "2026-10-11T00:00:00+00:00")
    await _snap(11000.0, "2026-10-11T05:00:00+00:00")  # 峰值
    await _snap(10450.0, "2026-10-11T09:00:00+00:00")  # 当前 = 从峰值回落 5%

    result = await drawdown()

    assert result.known is True
    assert result.peak_equity == pytest.approx(11000.0)
    assert result.current_equity == pytest.approx(10450.0)
    assert result.pct == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_drawdown_is_never_negative_at_a_new_high(db):
    """创新高时回撤是 0，不是负数 —— 负数会让"回撤 > 5%"这类判据永远不成立。"""
    from agent.portfolio.pnl import drawdown

    await _snap(10000.0, "2026-10-11T00:00:00+00:00")
    await _snap(10500.0, "2026-10-11T09:00:00+00:00")

    result = await drawdown()

    assert result.pct == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_reset_moves_the_peak_forward(db):
    """**关键**：重置点之前的快照一律不再作数，否则重置毫无意义。"""
    from agent.portfolio.pnl import drawdown, reset_drawdown

    await _snap(11000.0, "2026-10-11T00:00:00+00:00")  # 旧峰值，重置后必须被排除
    await _snap(9800.0, "2026-10-11T09:00:00+00:00")
    before = await drawdown()
    assert before.peak_equity == pytest.approx(11000.0)
    assert before.pct == pytest.approx((11000.0 - 9800.0) / 11000.0)

    # 重置点在 06:00：00:00 那张被排除，07:00 那张成为新基准
    await reset_drawdown(reason="测试：人工确认后重开", now=datetime(2026, 10, 11, 6, 0, tzinfo=UTC))
    await _snap(10000.0, "2026-10-11T07:00:00+00:00")

    after = await drawdown()
    assert after.peak_equity == pytest.approx(10000.0)
    assert after.pct == pytest.approx((10000.0 - 9800.0) / 10000.0)
    assert after.since is not None


@pytest.mark.asyncio
async def test_just_reset_with_nothing_after_it_is_unknown(db):
    """刚重置完、还没有新快照时：算不出来 → 风控会拦开仓。

    这是**有意**的保守失败。它不是死锁：每轮 tick 开头都会抓一张快照，
    下一个 tick 就有基准了（见 `supervisor._capture_equity_snapshot`）。
    """
    from agent.portfolio.pnl import drawdown, reset_drawdown

    await _snap(11000.0, "2026-10-11T00:00:00+00:00")
    await reset_drawdown(reason="测试：人工确认后重开", now=datetime(2026, 10, 11, 10, 0, tzinfo=UTC))

    result = await drawdown()

    assert result.known is False
    assert result.pct is None


@pytest.mark.asyncio
async def test_reset_is_recorded_not_by_deleting_history(db):
    """重置写的是一个时刻，快照全留着 —— 权益历史不该被抹掉。"""
    from agent.portfolio.pnl import reset_drawdown
    from agent.storage.repository import EquityRepository

    await _snap(10000.0, "2026-10-11T00:00:00+00:00")
    await reset_drawdown(reason="测试：人工确认后重开", now=datetime(2026, 10, 11, 6, 0, tzinfo=UTC))

    assert len(await EquityRepository().list_recent(limit=10)) == 1


@pytest.mark.asyncio
async def test_non_positive_peak_is_refused_not_divided(db):
    """峰值非正时不算比例 —— 除下去会得到无意义的数。"""
    from agent.portfolio.pnl import drawdown

    await _snap(0.0, "2026-10-11T00:00:00+00:00")
    await _snap(0.0, "2026-10-11T09:00:00+00:00")

    result = await drawdown()

    assert result.known is False
    assert "没有意义" in result.reason
