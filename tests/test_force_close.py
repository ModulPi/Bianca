"""强制平仓链路（A-3 / ADR-027）。全程不出网、不碰真库。

这条路径是全系统**唯一**不由 LLM 信号驱动的下单入口，所以要盯死三件事：
  ① 只在真破线时才动手；② 真的走了 `run_execute_agent`（记账一条不少）；
  ③ 记录里说得出"这是强制平仓"，而不是含糊成一次普通交易。
"""

from __future__ import annotations

import json

import pytest

from agent.config import Settings
from agent.graph import execute_agent
from agent.risk import close as close_module
from agent.risk.params import RISK_PARAM_FIELDS, RiskParams
from agent.storage import database
from agent.storage.repository import (
    EquityRepository,
    OrderRepository,
    RiskEventRepository,
    TradeRepository,
)

# 巡检时"观测到的近期高点"。用常量是为了让用例一眼看出跌了多少。
HIGH = 100000.0

# 平仓的成交回执。`filled` 与持仓数量一致，好断言"全平了"。
_FILLED_SELL = {
    "id": 77001,
    "status": "closed",
    "filled": 0.001,
    "average": 50000.0,
    "cost": 50.0,
    "fee": None,
    "clientOrderId": "cid",
}


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    monkeypatch.setattr(execute_agent, "_RECONCILE_DELAY_S", 0.0)
    await database.init_db()
    yield
    await database.close_db()


class FakeCCXT:
    """假 ccxt：够 `run_execute_agent` 走完提交前回查、下单、对账。"""

    def __init__(self, order):
        self.created: list[dict] = []
        self.probed: list[str] = []
        self._order = order

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.created.append({"symbol": symbol, "side": side, "amount": amount})
        return self._order

    async def fetch_order(self, order_id, symbol=None, params=None):
        import ccxt

        if (params or {}).get("origClientOrderId"):
            self.probed.append(params["origClientOrderId"])
            # 交易所不认得这个 cid —— 正常结果，可以安全提交
            raise ccxt.OrderNotFound("unknown cid")
        return self._order

    async def close(self):
        return None


def _install(monkeypatch, fake):
    class FakeDemo:
        def __init__(self, settings=None):
            self._exchange = fake

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def exchange(self):
            return self._exchange

        async def fetch_order(self, order_id, symbol=None):
            return await fake.fetch_order(order_id, symbol)

    monkeypatch.setattr(execute_agent, "SpotDemoExchange", FakeDemo)


async def _snapshot(*, positions, equity=1000.0, captured_at="2026-10-11T09:00:00+00:00"):
    held = sum(p["value_quote"] or 0.0 for p in positions)
    await EquityRepository().save(
        cash_quote=equity - held,
        holdings_value=held,
        equity=equity,
        positions=positions,
        unpriced=[],
        gaps=[],
        notes=[],
        captured_at=captured_at,
    )


def _btc(qty=0.001, price=50000.0):
    return {"asset": "BTC", "quantity": qty, "price": price, "value_quote": qty * price}


def _settings(**over):
    base = {
        "stop_loss_pct": 0.10,
        "max_drawdown_pct": 0.05,
        # 故意压得极小：平仓必须豁免它（ADR-027），否则这条用例会假绿
        "max_trade_amount": 5.0,
        "force_close_enabled": True,
    }
    base.update(over)
    return Settings(**base)


def _params(**over) -> RiskParams:
    """目标确认时固化的那份边界（ADR-021）。"""
    values = {name: getattr(_settings(), name) for name in RISK_PARAM_FIELDS}
    values.update(over)
    return RiskParams.from_snapshot(values)


@pytest.fixture(autouse=True)
def _stub_highs(monkeypatch):
    """掐断取高点这条唯一的出网路径 —— 风控裁决不该看代理脸色。"""

    async def _loader(settings, *, highs_provider=None):
        from agent.portfolio.pnl import drawdown
        from agent.risk.engine import PortfolioInputs, _latest_holdings

        equity, positions = await _latest_holdings()
        dd = await drawdown()
        return PortfolioInputs(
            equity=equity,
            positions=positions,
            highs={p.asset: HIGH for p in positions},
            drawdown_pct=dd.pct,
            peak_equity=dd.peak_equity,
        )

    monkeypatch.setattr(close_module, "load_portfolio_inputs", _loader)


# ---------------------------------------------------------------- 没破线就什么都不做


@pytest.mark.asyncio
async def test_no_order_when_nothing_is_breached(db, monkeypatch):
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=98000.0)])  # 从 100000 只跌 2%

    results = await close_module.force_close(settings=_settings())

    assert results == []
    assert fake.created == []


@pytest.mark.asyncio
async def test_no_positions_means_no_work(db, monkeypatch):
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[])

    assert await close_module.force_close(settings=_settings()) == []
    assert fake.created == []


# ---------------------------------------------------------------- 破线就平


@pytest.mark.asyncio
async def test_stop_breach_sells_the_whole_position(db, monkeypatch):
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(qty=0.001, price=50000.0)])  # 从 100000 跌一半

    results = await close_module.force_close(settings=_settings())

    assert len(results) == 1
    assert results[0]["rule"] == "stop_loss"
    assert fake.created[0]["side"] == "sell"
    assert fake.created[0]["amount"] == pytest.approx(0.001)  # 基础币全量
    assert fake.created[0]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_close_bypasses_the_per_trade_cap(db, monkeypatch):
    """**关键**：单笔上限压到 5 USDT，而持仓值 50 USDT —— 平仓仍必须成功。

    受上限约束的话，仓位越大越平不掉，"保护"就成了空话（ADR-027）。
    """
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(qty=0.001, price=50000.0)])

    results = await close_module.force_close(settings=_settings(max_trade_amount=5.0))

    assert len(fake.created) == 1
    assert results[0]["status"] in {"filled", "unknown"}


@pytest.mark.asyncio
async def test_drawdown_breach_closes_everything(db, monkeypatch):
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    # 先写一个高权益峰值，再写本次的低权益 —— 回撤由此可见
    await _snapshot(positions=[], equity=100.0, captured_at="2026-10-11T00:00:00+00:00")
    await _snapshot(
        positions=[
            {"asset": "BTC", "quantity": 0.001, "price": 50000.0, "value_quote": 50.0},
            {"asset": "ETH", "quantity": 0.01, "price": 3000.0, "value_quote": 30.0},
        ],
        equity=77.0,
    )

    results = await close_module.force_close(settings=_settings())

    assert {r["asset"] for r in results} == {"BTC", "ETH"}
    assert all(r["rule"] == "drawdown" for r in results)
    assert {c["side"] for c in fake.created} == {"sell"}


@pytest.mark.asyncio
async def test_force_close_switch_off_does_nothing(db, monkeypatch):
    """总闸关掉时即使破线也不下单。"""
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=50000.0)])

    from agent.graph.supervisor import _force_close_if_needed

    assert await _force_close_if_needed(_settings(force_close_enabled=False)) == []
    assert fake.created == []


@pytest.mark.asyncio
async def test_the_patrol_uses_the_goal_snapshot_not_the_live_config(db, monkeypatch):
    """**关键**（ADR-021）：持仓从高点跌了 50%，配置里的止损线 10% 会说"平"，
    但目标确认时那份快照写的是 90% —— 那就**不许平**。

    这条用例挡的是"改了 `.env` 就绕过了固化"：配置与快照在这里结论相反，
    谁生效一眼就能看出来。
    """
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=50000.0)])

    results = await close_module.force_close(
        settings=_settings(stop_loss_pct=0.10),
        params=_params(stop_loss_pct=0.90, max_drawdown_pct=0.90),
    )

    assert results == []
    assert fake.created == []


@pytest.mark.asyncio
async def test_the_goal_snapshot_also_owns_the_force_close_switch(db, monkeypatch):
    """`force_close_enabled` 是风控参数的一份，不是全局开关的家丁。"""
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=50000.0)])

    from agent.graph.supervisor import _force_close_if_needed

    results = await _force_close_if_needed(
        _settings(force_close_enabled=True),
        params=_params(force_close_enabled=False),
    )

    assert results == []
    assert fake.created == []


# ---------------------------------------------------------------- 记录说真话


@pytest.mark.asyncio
async def test_the_force_close_claims_the_goal_it_was_protecting(db, monkeypatch):
    """ADR-035：强平也要认领目标，并且来源说得清是风控而不是模型。"""
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=50000.0)])

    results = await close_module.force_close(
        settings=_settings(), goal_id="g-1", plan_id="p-1"
    )

    row = await TradeRepository().get_by_id(results[0]["trade_log_id"])
    assert (row.goal_id, row.plan_id) == ("g-1", "p-1")
    # 来源变了，但裁决结论不能跟着变 —— 它仍然不是一次"风控通过"的普通交易。
    assert row.risk_decision == "force_close"

    events = await RiskEventRepository().list_recent(limit=5)
    detail = json.loads(events[0].detail)
    assert detail["origin"] == "risk_force_close"
    assert (detail["goal_id"], detail["plan_id"]) == ("g-1", "p-1")


@pytest.mark.asyncio
async def test_the_event_records_the_exemption(db, monkeypatch):
    """**关键**：绕过了哪道闸门必须留痕，不能是隐式放行。"""
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=50000.0)])

    await close_module.force_close(settings=_settings())

    events = await RiskEventRepository().list_recent(limit=5)
    assert len(events) == 1
    assert events[0].event_type == "stop_loss"
    detail = json.loads(events[0].detail)
    assert detail["force_close"] is True
    assert "max_trade_amount" in detail["exempt_rules"]
    assert detail["exempt_reason"]


@pytest.mark.asyncio
async def test_the_trade_log_says_force_close_not_approved(db, monkeypatch):
    """**关键**：不能把强制平仓记成一次"风控通过"的普通交易（ADR-019 可归因）。"""
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=50000.0)])

    results = await close_module.force_close(settings=_settings())

    row = await TradeRepository().get_by_id(results[0]["trade_log_id"])
    assert row is not None
    assert row.risk_decision == "force_close"
    assert "强制平仓" in row.risk_reason


@pytest.mark.asyncio
async def test_close_all_ignores_the_thresholds_and_keeps_its_own_origin(db, monkeypatch):
    """达成清仓（ADR-038）：阈值完全不参与，来源也不能写成"强平"。"""
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=50000.0)])

    results = await close_module.force_close(
        settings=_settings(),
        # 两条线都远未触及 —— 普通巡检什么都不会做
        params=_params(stop_loss_pct=0.99, max_drawdown_pct=0.99),
        goal_id="g-1",
        plan_id="p-1",
        close_all=True,
        origin=close_module.ORIGIN_GOAL_ACHIEVED,
    )

    assert len(results) == 1
    assert results[0]["rule"] == "goal_achieved"
    assert fake.created, "达成清仓也必须真的走到下单"

    row = await TradeRepository().get_by_id(results[0]["trade_log_id"])
    assert row.risk_decision == "goal_achieved"
    assert "目标达成" in row.risk_reason
    assert (row.goal_id, row.plan_id) == ("g-1", "p-1")

    events = await RiskEventRepository().list_recent(limit=5)
    detail = json.loads(events[0].detail)
    assert detail["origin"] == "goal_achieved"


@pytest.mark.asyncio
async def test_manual_close_is_neither_achievement_nor_stop_out(db, monkeypatch):
    """用户指令下的人工全平：不看阈值，但来源必须独立（ADR-035 的同一理由）。

    记成 `goal_achieved` 会让"这个目标达成过"凭空多出一次；记成
    `risk_force_close` 则会污染"强制平仓"的统计。两者都不是事实。
    """
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(price=50000.0)])

    results = await close_module.force_close(
        settings=_settings(),
        params=_params(stop_loss_pct=0.99, max_drawdown_pct=0.99),
        close_all=True,
        origin=close_module.ORIGIN_MANUAL_CLOSE,
    )

    assert len(results) == 1
    assert results[0]["rule"] == "manual_close"
    assert fake.created, "人工全平也必须真的走到下单"

    row = await TradeRepository().get_by_id(results[0]["trade_log_id"])
    assert row.risk_decision == "manual_close"
    assert "人工全平" in row.risk_reason

    events = await RiskEventRepository().list_recent(limit=5)
    detail = json.loads(events[0].detail)
    assert detail["origin"] == "manual_close"


@pytest.mark.asyncio
async def test_the_order_lands_in_the_ledger_like_any_other(db, monkeypatch):
    """紧急不是跳过记账的理由 —— 恰恰相反，事后最需要查的就是这一笔。"""
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(qty=0.001, price=50000.0)])

    await close_module.force_close(settings=_settings())

    orders = await OrderRepository().list_recent(limit=5)
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].amount_kind == "base"
    assert orders[0].client_order_id  # 幂等键照样有
    assert fake.probed  # 提交前照样回查过


@pytest.mark.asyncio
async def test_re_running_the_patrol_on_a_stale_snapshot_orders_twice(db, monkeypatch):
    """**已知边界，不是有意设计**：拿着同一张陈旧快照连跑两次，会真的下两笔。

    幂等键由 `trade_id` 派生，而每次巡检都生成新的 `trade_id`，所以第二笔换了键，
    交易所侧也不会当成重复。守卫不在这一层，而在上面：巡检每轮只跑一次
    （`supervisor._force_close_if_needed`），且下一轮的快照是在下一轮开头**重新采集**的
    —— 那时持仓已经没了。真正要防的是"不该平的时候别平"（见本文件开头的用例）。

    这条用例把现状钉住，免得有人以为这一层已经去重了。
    """
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)
    await _snapshot(positions=[_btc(qty=0.001, price=50000.0)])

    await close_module.force_close(settings=_settings())
    await close_module.force_close(settings=_settings())

    assert len(fake.created) == 2
