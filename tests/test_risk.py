import pytest

from agent.config import Settings
from agent.portfolio.pnl import DailyPnl
from agent.risk.engine import RiskEngine
from agent.risk.params import RiskParams
from agent.risk.rules import (
    DrawdownRule,
    MaxTradeAmountRule,
    PositionLimitRule,
    RiskContext,
    RiskVerdict,
    TrailingStopRule,
)


@pytest.fixture(autouse=True)
def no_live_klines(monkeypatch):
    """默认掐断"取近期高点"这条唯一的出网路径。

    单测一旦真打生产 REST，既慢又看代理脸色 —— 而风控的裁决不该依赖网络是否通。
    要验取数的用例**显式注入** `highs_provider`，或直接测 `agent/portfolio/klines.py`
    （那里有独立打桩的用例）。默认返回"都拿不到"，因此任何忘记注入的用例
    会看到"保护不成立"这条保守分支，而不是一个侥幸通过的绿。
    """

    async def _none(assets, **_kwargs):
        return {a: None for a in assets}

    monkeypatch.setattr("agent.risk.engine.fetch_recent_highs", _none)


def _settings(**overrides) -> Settings:
    base = {
        "max_trade_amount": 50.0,
        "daily_loss_limit": 100.0,
    }
    base.update(overrides)
    return Settings(**base)


def _params(**overrides) -> RiskParams:
    """直接构造 `RiskContext` 时用的那套边界（与从 settings 构造等价）。"""
    return RiskParams.from_settings(_settings(**overrides))


def test_max_trade_amount_rejects_large_buy():
    rule = MaxTradeAmountRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 80.0},
        market_data={"last": 65000},
        params=_params(),
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.approved is False
    assert verdict.rule == "max_trade_amount"


def test_max_trade_amount_passes_small_buy():
    rule = MaxTradeAmountRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 30.0},
        market_data={"last": 65000},
        params=_params(),
    )
    assert rule.evaluate(ctx) is None


def _stub_pnl(monkeypatch, snapshot):
    """替换引擎取日盈亏的那一步。引擎读的是权益口径，所以这里塞的是 DailyPnl。"""
    from agent.risk import engine as engine_module

    async def fake() -> object:
        return snapshot

    monkeypatch.setattr(engine_module, "compute_daily_pnl", fake)


@pytest.mark.asyncio
async def test_risk_engine_daily_loss_rejects(monkeypatch):
    _stub_pnl(monkeypatch, DailyPnl(known=True, pnl=-150.0, baseline_equity=10000.0, current_equity=9850.0))

    verdict = await RiskEngine(_settings()).evaluate(
        {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT", "reason": "t"},
        {"last": 65000},
    )
    assert verdict.approved is False
    assert verdict.rule == "daily_loss"


@pytest.mark.asyncio
async def test_unrealized_loss_counts_toward_the_breaker(monkeypatch):
    """**关键**：权益口径下持仓浮亏也算进去（用户 2026-10-11 决定）。

    同一天的两次快照之间没有任何成交，只因为持仓跌了就触发熔断 ——
    这正是"别在下行里继续加仓"想要的。
    """
    _stub_pnl(monkeypatch, DailyPnl(known=True, pnl=-120.0))

    verdict = await RiskEngine(_settings(daily_loss_limit=100.0)).evaluate(
        {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT", "reason": "t"},
        {"last": 65000},
    )
    assert verdict.approved is False
    assert verdict.rule == "daily_loss"


@pytest.mark.asyncio
async def test_unknown_daily_pnl_blocks_instead_of_passing(monkeypatch):
    """算不出日盈亏时**拦住**，不是放行。

    旧的现金流累加器在新的一天返回 0.0，于是"不知道"被读成了"没亏"，
    熔断在最需要它的那一刻（当天第一笔单）恰好是哑的。
    """
    _stub_pnl(monkeypatch, DailyPnl(known=False, reason="今天还没有权益快照"))

    verdict = await RiskEngine(_settings()).evaluate(
        {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT", "reason": "t"},
        {"last": 65000},
    )
    assert verdict.approved is False
    assert verdict.rule == "daily_loss"
    assert "未知" in verdict.reason


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from agent.storage import database

    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


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
async def test_engine_reads_the_real_equity_snapshots(db):
    """**接线测试**：引擎 → 权益快照表，中间不打桩。

    前面几个用例把 daily_pnl 换成了桩，证明的是规则本身；这一个证明
    "引擎真的在从权益算盈亏"，而不是还在读那个被删掉的现金流累加器。
    """
    from datetime import UTC, datetime

    await _snap(10000.0, "2026-10-11T00:00:10+00:00")
    await _snap(9800.0, "2026-10-11T08:00:00+00:00")  # 浮亏 200，超过 100 的限额

    now = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
    import agent.portfolio.pnl as pnl_module

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    orig = pnl_module.datetime
    pnl_module.datetime = _FixedDatetime
    try:
        verdict = await RiskEngine(_settings(daily_loss_limit=100.0)).evaluate(
            {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT", "reason": "t"},
            {"last": 65000},
        )
    finally:
        pnl_module.datetime = orig

    assert verdict.approved is False
    assert verdict.rule == "daily_loss"


@pytest.mark.asyncio
async def test_engine_refuses_when_there_are_no_snapshots_yet(db):
    """空库 → 日盈亏未知 → 拦住。这是新装/宕机恢复后第一笔单的真实处境。"""
    verdict = await RiskEngine(_settings()).evaluate(
        {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT", "reason": "t"},
        {"last": 65000},
    )
    assert verdict.approved is False
    assert verdict.rule == "daily_loss"


@pytest.mark.asyncio
async def test_breaker_lets_you_reduce_but_not_add(monkeypatch):
    """**关键**：熔断只拦开仓，放行减仓（用户 2026-10-11 决定）。

    拦 SELL 会把仓位锁死在下跌里 —— 熔断反倒成了最大的风险源。
    """
    _stub_pnl(monkeypatch, DailyPnl(known=True, pnl=-500.0))
    engine = RiskEngine(_settings(daily_loss_limit=100.0))
    market = {"last": 65000}

    buy = await engine.evaluate({"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT"}, market)
    sell = await engine.evaluate(
        # 0.0002 × 65000 = 13 USDT，在 50 的上限内 —— 要验证的是熔断放行，
        # 不是金额上限，所以这笔必须本身就合规。
        {"action": "SELL", "amount": 0.0002, "symbol": "BTCUSDT"}, market
    )

    assert buy.approved is False
    assert buy.rule == "daily_loss"
    assert sell.approved is True


@pytest.mark.asyncio
async def test_unknown_pnl_also_lets_you_reduce(monkeypatch):
    """"不知道"同样不能把人锁住 —— 未知的处置与熔断一致。"""
    _stub_pnl(monkeypatch, DailyPnl(known=False, reason="今天还没有权益快照"))

    verdict = await RiskEngine(_settings()).evaluate(
        {"action": "SELL", "amount": 0.0002, "symbol": "BTCUSDT"}, {"last": 65000}
    )

    assert verdict.approved is True


@pytest.mark.asyncio
async def test_letting_a_sell_through_does_not_also_waive_the_size_cap(monkeypatch):
    """放行 SELL 不代表风控被绕过：单笔金额上限对卖出照样生效。"""
    _stub_pnl(monkeypatch, DailyPnl(known=True, pnl=-500.0))

    verdict = await RiskEngine(_settings(max_trade_amount=50.0)).evaluate(
        {"action": "SELL", "amount": 0.01, "symbol": "BTCUSDT"},  # ≈650 USDT
        {"last": 65000},
    )

    assert verdict.approved is False
    assert verdict.rule == "max_trade_amount"


# ---------------------------------------------------------------- 仓位上限（A-3）


def _pos(asset="BTC", qty=0.001, price=65000.0, value=65.0):
    from agent.portfolio.valuation import Position

    return Position(asset=asset, quantity=qty, price=price, value_quote=value)


def _ctx(action="BUY", amount=10.0, *, equity=1000.0, positions=(), params=None):
    return RiskContext(
        signal={"action": action, "amount": amount, "symbol": "BTCUSDT"},
        market_data={"symbol": "BTCUSDT", "last": 65000.0},
        params=params or _params(max_position_pct=0.5, max_trade_amount=10_000.0),
        equity=equity,
        positions=tuple(positions),
    )


def test_position_limit_passes_when_result_stays_under():
    rule = PositionLimitRule()
    # 1000 权益的 50% = 500；买后 65 + 10 = 75
    assert rule.evaluate(_ctx(amount=10.0, positions=[_pos()])) is None


def test_position_limit_rejects_a_buy_that_would_exceed():
    rule = PositionLimitRule()
    ctx = _ctx(amount=100.0, positions=[_pos(value=450.0)])
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.approved is False
    assert verdict.rule == "position_limit"


def test_position_limit_judges_the_after_trade_state():
    """**关键**：判的是买后，不是买前 —— 否则可以踩着线一点点加上去。"""
    rule = PositionLimitRule()
    # 买前 480 < 500 上限，买后 490 仍在限内 → 放
    assert rule.evaluate(_ctx(amount=10.0, positions=[_pos(value=480.0)])) is None
    # 买后 520 越线 → 拦（买前 510 也已经越了，但拦住它的理由是同一个）
    assert rule.evaluate(_ctx(amount=10.0, positions=[_pos(value=510.0)])) is not None
    # 这正是"买前合法、买后越线"的一笔
    assert rule.evaluate(_ctx(amount=30.0, positions=[_pos(value=480.0)])) is not None


def test_position_limit_ignores_sells():
    """减仓永远放行（与 ADR-025 同向）。"""
    rule = PositionLimitRule()
    verdict = rule.evaluate(_ctx(action="SELL", amount=0.001, positions=[_pos(value=900.0)]))
    assert verdict is None


def test_position_limit_refuses_without_equity():
    """拿不到权益就算不出上限 —— 拦，不是放。"""
    rule = PositionLimitRule()
    verdict = rule.evaluate(_ctx(equity=None))
    assert verdict is not None
    assert "拿不到权益" in verdict.reason


def test_position_limit_refuses_when_the_holding_is_unpriced():
    """已有持仓取不到价 → 它占多少权益算不出来 → 拦。"""
    rule = PositionLimitRule()
    verdict = rule.evaluate(_ctx(positions=[_pos(price=None, value=None)]))
    assert verdict is not None
    assert "取不到价" in verdict.reason


def test_position_limit_refuses_an_unrecognisable_symbol():
    rule = PositionLimitRule()
    ctx = _ctx()
    ctx.signal["symbol"] = "WEIRDPAIR"
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert "无法识别交易对" in verdict.reason


def test_position_limit_only_counts_the_base_asset():
    """持有的 ETH 不该为 BTC 的买单背锅。"""
    rule = PositionLimitRule()
    assert rule.evaluate(_ctx(amount=10.0, positions=[_pos(asset="ETH", value=900.0)])) is None


@pytest.mark.asyncio
async def test_engine_reads_positions_from_the_snapshot(db):
    """**接线测试**：引擎 → 快照的 positions 列。中间不打桩。

    快照里 BTC 已值 480 USDT，权益 1000，上限 50%。买 30 USDT 会到 510 → 必须拦。
    如果引擎没把持仓读进来，这笔会被放行 —— 那正是这个用例要抓的。
    """
    from agent.storage.repository import EquityRepository

    await EquityRepository().save(
        cash_quote=520.0,
        holdings_value=480.0,
        equity=1000.0,
        positions=[
            {"asset": "BTC", "quantity": 0.001, "price": 480000.0, "value_quote": 480.0}
        ],
        unpriced=[],
        gaps=[],
        notes=[],
        captured_at="2026-10-11T09:00:00+00:00",
    )

    engine = RiskEngine(
        _settings(max_position_pct=0.5, max_trade_amount=10_000.0),
        rules=[PositionLimitRule()],
    )
    verdict = await engine.evaluate(
        {"action": "BUY", "amount": 30.0, "symbol": "BTCUSDT"}, {"last": 480000.0}
    )

    assert verdict.approved is False
    assert verdict.rule == "position_limit"


@pytest.mark.asyncio
async def test_engine_treats_a_corrupt_positions_blob_as_empty_not_fatal(db, caplog):
    """库里留着解析不了的 positions 时不许崩，但也不许静默。"""
    from agent.storage.repository import EquityRepository

    await EquityRepository().save(
        cash_quote=1000.0,
        holdings_value=0.0,
        equity=1000.0,
        positions=[{"asset": "BTC"}],  # 缺 quantity 之外还整体是坏 JSON 的前身
        unpriced=[],
        gaps=[],
        notes=[],
    )
    # 直接把列改成非 JSON，模拟旧版本/手改留下的行
    import sqlite3  # noqa: E402  — 只在这个用例里直接改库

    from agent.storage import database

    url = database.get_settings().database_url
    with sqlite3.connect(url.split("///")[-1]) as conn:
        conn.execute("UPDATE equity_snapshots SET positions = 'not json'")
        conn.commit()

    engine = RiskEngine(_settings(), rules=[PositionLimitRule()])
    with caplog.at_level("WARNING"):
        verdict = await engine.evaluate(
            {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT"}, {"last": 65000.0}
        )

    assert verdict.approved is True  # 按无持仓处理 → 10 < 500
    assert "解析失败" in caplog.text


# ---------------------------------------------------------------- 止损线（A-3）


def _stop_ctx(*, price=65000.0, high=65000.0, action="BUY", positions=None, params=None):
    return RiskContext(
        signal={"action": action, "amount": 0.0002, "symbol": "BTCUSDT"},
        # 故意放一个**与快照价不同**的 Demo 价，见 test_stop_ignores_the_demo_price
        market_data={"symbol": "BTCUSDT", "last": 1.0},
        params=params or _params(stop_loss_pct=0.10, max_trade_amount=10_000.0),
        equity=1000.0,
        positions=tuple(
            [_pos(qty=0.001, price=price, value=0.001 * price)]
            if positions is None
            else positions
        ),
        highs={"BTC": high},
    )


def test_stop_passes_when_nothing_held():
    rule = TrailingStopRule()
    assert rule.evaluate(_stop_ctx(positions=[])) is None


def test_stop_passes_a_shallow_dip():
    rule = TrailingStopRule()
    # 从 65000 跌到 64000 = 1.5%，远没到 10%
    assert rule.evaluate(_stop_ctx(price=64000.0, high=65000.0)) is None


def test_stop_fires_past_the_threshold():
    rule = TrailingStopRule()
    verdict = rule.evaluate(_stop_ctx(price=58000.0, high=65000.0))  # 回落 10.8%
    assert verdict is not None
    assert verdict.approved is False
    assert verdict.rule == "stop_loss"


def test_stop_fires_exactly_at_the_threshold():
    """到线即破 —— 用 >= 而不是 >，免得在边界上漏一次。"""
    rule = TrailingStopRule()
    verdict = rule.evaluate(_stop_ctx(price=58500.0, high=65000.0))  # 正好 10%
    assert verdict is not None


def test_stop_refuses_when_the_high_is_unknown():
    """**关键**：拿不到高点 = 这道保护不成立 = 拦，而不是当作没跌。"""
    rule = TrailingStopRule()
    ctx = _stop_ctx()
    ctx.highs = {}
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert "取不到" in verdict.reason


def test_stop_ignores_the_demo_price():
    """**关键**：判止损用的是快照里的**实盘**价，不是 market_data 里的 Demo 价。

    这里 market_data["last"] 被设成荒谬的 1.0：如果规则读了它，会得出"回落 99.99%"
    而误判破线。它没误判，就证明两个数同源（都是生产 REST），没混用。
    """
    rule = TrailingStopRule()
    assert rule.evaluate(_stop_ctx(price=65000.0, high=65000.0)) is None


def test_stop_leaves_sells_alone():
    rule = TrailingStopRule()
    assert rule.evaluate(_stop_ctx(price=1000.0, high=65000.0, action="SELL")) is None


def test_stop_ignores_other_assets_holdings():
    """持有 ETH 不影响对 BTC 的加仓判断。"""
    rule = TrailingStopRule()
    ctx = _stop_ctx(positions=[_pos(asset="ETH", qty=1.0, price=3000.0, value=3000.0)])
    ctx.highs = {"BTC": 65000.0}
    assert rule.evaluate(ctx) is None


# ---------------------------------------------------------------- 回撤（A-3）


def _dd_ctx(*, pct=0.0, action="BUY", params=None):
    return RiskContext(
        signal={"action": action, "amount": 0.0002, "symbol": "BTCUSDT"},
        market_data={"symbol": "BTCUSDT", "last": 65000.0},
        params=params or _params(max_drawdown_pct=0.05, max_trade_amount=10_000.0),
        equity=1000.0,
        drawdown_pct=pct,
        peak_equity=1000.0,
    )


def test_drawdown_passes_within_the_limit():
    assert DrawdownRule().evaluate(_dd_ctx(pct=0.02)) is None


def test_drawdown_fires_past_the_limit():
    verdict = DrawdownRule().evaluate(_dd_ctx(pct=0.06))
    assert verdict is not None
    assert verdict.rule == "drawdown"
    assert "回撤" in verdict.reason


def test_drawdown_refuses_when_it_cannot_be_computed():
    """**关键**：算不出回撤就拦，与日亏熔断同一条原则。"""
    verdict = DrawdownRule().evaluate(_dd_ctx(pct=None))
    assert verdict is not None
    assert verdict.approved is False


def test_drawdown_leaves_sells_alone():
    """破线时更要让人跑得掉 —— 拦 SELL 会把人锁死（ADR-025/027）。"""
    assert DrawdownRule().evaluate(_dd_ctx(pct=0.9, action="SELL")) is None


@pytest.mark.asyncio
async def test_engine_injects_highs_and_the_stop_fires(db):
    """**接线测试**：引擎 → 取高点 → 止损线。中间只有取数被打桩。"""
    from agent.storage.repository import EquityRepository

    await EquityRepository().save(
        cash_quote=900.0,
        holdings_value=100.0,
        equity=1000.0,
        positions=[
            {"asset": "BTC", "quantity": 0.001, "price": 100000.0, "value_quote": 100.0}
        ],
        unpriced=[],
        gaps=[],
        notes=[],
    )

    seen: list[list[str]] = []

    async def highs(assets):
        seen.append(list(assets))
        return {"BTC": 200000.0}  # 现价 100000，从高点回落 50%

    engine = RiskEngine(
        _settings(stop_loss_pct=0.10, max_trade_amount=10_000.0),
        rules=[TrailingStopRule()],
        highs_provider=highs,
    )
    verdict = await engine.evaluate(
        {"action": "BUY", "amount": 5.0, "symbol": "BTCUSDT"}, {"last": 100000.0}
    )

    assert seen == [["BTC"]]  # 只问持有的资产
    assert verdict.approved is False
    assert verdict.rule == "stop_loss"


@pytest.mark.asyncio
async def test_engine_makes_no_network_call_when_nothing_is_held(db):
    """空仓时不该为了取高点出网 —— 没有标的要看。"""
    from agent.storage.repository import EquityRepository

    await EquityRepository().save(
        cash_quote=1000.0, holdings_value=0.0, equity=1000.0,
        positions=[], unpriced=[], gaps=[], notes=[],
    )

    seen: list[list[str]] = []

    async def highs(assets):
        seen.append(list(assets))
        return {}

    engine = RiskEngine(
        _settings(max_trade_amount=10_000.0),
        rules=[TrailingStopRule()],
        highs_provider=highs,
    )
    verdict = await engine.evaluate(
        {"action": "BUY", "amount": 5.0, "symbol": "BTCUSDT"}, {"last": 65000.0}
    )

    assert seen == [[]]  # 被调用，但问的是空列表 —— 默认实现会直接返回，不出网
    assert verdict.approved is True


@pytest.mark.asyncio
async def test_engine_wires_the_real_drawdown_from_snapshots(db):
    """**接线测试**：引擎 → 快照峰值 → 回撤熔断。不打桩。"""
    from agent.storage.repository import EquityRepository

    await EquityRepository().save(
        cash_quote=10000.0, holdings_value=0.0, equity=10000.0,
        positions=[], unpriced=[], gaps=[], notes=[],
        captured_at="2026-10-11T00:00:00+00:00",
    )
    await EquityRepository().save(  # 从峰值回落 6%，超过 5% 上限
        cash_quote=9400.0, holdings_value=0.0, equity=9400.0,
        positions=[], unpriced=[], gaps=[], notes=[],
        captured_at="2026-10-11T09:00:00+00:00",
    )

    engine = RiskEngine(
        _settings(max_drawdown_pct=0.05, max_trade_amount=10_000.0),
        rules=[DrawdownRule()],
    )
    verdict = await engine.evaluate(
        {"action": "BUY", "amount": 5.0, "symbol": "BTCUSDT"}, {"last": 65000.0}
    )

    assert verdict.approved is False
    assert verdict.rule == "drawdown"
    assert "6.00%" in verdict.reason
