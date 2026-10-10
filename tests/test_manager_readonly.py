"""阶段 B-2：经理的只读工具集。

全程临时库（主库 + 行情库各一个），不出网、不写真实库。

这里最要紧的两类断言：
  · **每个工具都是只读的** —— 验收条件是「不产生任何订单」，这条得在测试里钉死
  · **读不到就写读不到** —— 不能用 0 / [] 冒充「不知道」
"""

from __future__ import annotations

import json

import pytest

from agent.config import Settings
from agent.manager.readonly import build_readonly_registry
from agent.market import storage as market_storage
from agent.market.repository import KlineRepository
from agent.storage import database
from agent.storage.database import close_db, init_db
from agent.storage.repository import (
    DecisionRepository,
    EquityRepository,
    RiskEventRepository,
    TradeRepository,
)

T0 = 1_700_000_000_000


@pytest.fixture
async def main_db(tmp_path, monkeypatch):
    await close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await init_db()
    yield
    await close_db()


@pytest.fixture
async def market_db(tmp_path, monkeypatch):
    await market_storage.close_market_db()
    path = tmp_path / "market.db"
    monkeypatch.setattr(
        market_storage,
        "get_settings",
        lambda: Settings(market_database_url=f"sqlite+aiosqlite:///{path.as_posix()}"),
    )
    await market_storage.init_market_db()
    yield
    await market_storage.close_market_db()


def _registry(settings: Settings | None = None):
    return build_readonly_registry(settings or Settings(trade_symbol="BTCUSDT"))


def _bar(index: int, *, symbol: str = "BTCUSDT", interval: str = "1m") -> dict:
    price = 100.0 + index
    return {
        "time": T0 + index * 60_000,
        "symbol": symbol,
        "interval": interval,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price + 0.5,
        "volume": 1.5,
        "quote_volume": 150.0,
        "trades": 10,
        "taker_buy_base": 0.7,
        "taker_buy_quote": 70.0,
    }


async def _snapshot(*, equity: float, captured_at: str, gaps: list | None = None, positions=None):
    return await EquityRepository().save(
        cash_quote=equity,
        holdings_value=0.0,
        equity=equity,
        positions=positions or [],
        unpriced=[],
        gaps=gaps or [],
        notes=[],
        captured_at=captured_at,
    )


# --- 只读这条纪律本身 -------------------------------------------------------


def test_every_registered_tool_is_read_only():
    """验收条件「不产生任何订单」的第一道保险：注册表里一个写工具都不许有。"""
    reg = _registry()
    assert len(reg) >= 6
    writes = [s.name for s in reg.specs() if not s.read_only]
    assert writes == [], f"这些工具会改状态，阶段 B 不该有：{writes}"


@pytest.mark.asyncio
async def test_no_order_capable_tool_is_reachable(main_db):
    """按名字点名常见的「会动钱」工具，一个都不该在。"""
    reg = _registry()
    for name in ("place_order", "create_order", "cancel_order", "close_position", "force_close"):
        assert name not in reg, f"{name} 不该出现在阶段 B 的工具集里"


def test_registry_refuses_to_be_opened_by_accident():
    """注册表默认拒绝写工具 —— 这层保护不能被顺手绕过。"""
    from agent.manager.tools import ToolRegistrationError, ToolSpec

    reg = _registry()

    async def _h(args: dict):
        return None

    with pytest.raises(ToolRegistrationError):
        reg.register(
            ToolSpec(
                name="place_order",
                description="下单",
                parameters={"type": "object", "properties": {}},
                handler=_h,
                read_only=False,
            )
        )


def test_system_prompt_for_the_real_registry_teaches_every_tool():
    import json as _json

    from agent.manager.prompt import build_system_prompt

    reg = _registry()
    prompt = build_system_prompt(reg)
    catalog = _json.loads(prompt.split("```json", 1)[1].split("```", 1)[0])
    assert {c["function"]["name"] for c in catalog} == set(reg.names())
    assert all(c["function"]["description"] for c in catalog), "每个工具都得有说明"


# --- 读不到 ≠ 是 0 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_portfolio_says_unknown_not_zero(main_db):
    reg = _registry()
    out = await reg.call("get_portfolio", {})
    assert out["available"] is False
    assert "reason" in out
    assert "equity" not in out, "没数据时绝不能给出一个看着像权益的 0"


@pytest.mark.asyncio
async def test_empty_market_says_unknown_not_empty_list(market_db):
    reg = _registry()
    out = await reg.call("get_market", {"symbol": "BTCUSDT"})
    assert out["available"] is False
    assert "last_price" not in out, "没有行情就不能报一个价格出来"


@pytest.mark.asyncio
async def test_drawdown_unknown_without_snapshots(main_db):
    """一张快照都没有 = 不知道。这不是「没回撤」，是「算不出来」。"""
    out = await _registry().call("get_drawdown", {})
    assert out["available"] is False
    assert "reason" in out


@pytest.mark.asyncio
async def test_drawdown_with_a_single_snapshot_is_zero_and_that_is_correct(main_db):
    """只有一张快照时峰值就是它自己，回撤确实是 0 —— 这里**不该**报不知道。

    口径跟 `pnl.drawdown()` 走。这条用例的存在是为了把这个选择写下来：
    它看起来像「0 和不知道混用」的那个坑，但它不是。
    """
    await _snapshot(equity=1000.0, captured_at="2026-10-11T00:00:00+00:00")
    out = await _registry().call("get_drawdown", {})
    assert out["available"] is True
    assert out["drawdown_pct"] == 0.0
    assert out["breached"] is False


@pytest.mark.asyncio
async def test_drawdown_honours_the_reset_point(main_db):
    """重置之后峰值只从那一刻往后看（ADR-029/030）。

    这条钉的是一个**很容易写错**的地方：直接取「全历史最高」，重置就形同虚设 ——
    经理会看到一个早就被确认过的旧高点算出来的虚高回撤。
    """
    from agent.portfolio.pnl import _DRAWDOWN_RESET_KEY
    from agent.storage.repository import AgentConfigRepository

    await _snapshot(equity=1000.0, captured_at="2026-10-11T00:00:00+00:00")
    await _snapshot(equity=900.0, captured_at="2026-10-11T02:00:00+00:00")
    reg = _registry()

    before = await reg.call("get_drawdown", {})
    assert before["peak_equity"] == 1000.0
    assert before["drawdown_pct"] == pytest.approx(0.10)
    assert before["peak_since"] is None

    await AgentConfigRepository().set(_DRAWDOWN_RESET_KEY, "2026-10-11T01:00:00+00:00")

    after = await reg.call("get_drawdown", {})
    assert after["peak_equity"] == 900.0, "重置点之前的峰值不该再作数"
    assert after["drawdown_pct"] == 0.0
    assert after["breached"] is False
    assert after["peak_since"] == "2026-10-11T01:00:00+00:00"


@pytest.mark.asyncio
async def test_empty_history_says_unknown(main_db):
    reg = _registry()
    for name, key in (
        ("get_recent_decisions", "decisions"),
        ("get_recent_trades", "trades"),
        ("get_recent_risk_events", "events"),
    ):
        out = await reg.call(name, {})
        assert out["available"] is False, name
        assert key not in out, name


# --- 读到的时候给得对不对 ---------------------------------------------------


@pytest.mark.asyncio
async def test_portfolio_reads_the_latest_snapshot(main_db):
    await _snapshot(equity=1000.0, captured_at="2026-10-11T00:00:00+00:00")
    await _snapshot(
        equity=940.0,
        captured_at="2026-10-11T01:00:00+00:00",
        positions=[{"asset": "BTC", "quantity": 0.004, "price": 50000.0, "value_quote": 200.0}],
    )
    out = await _registry().call("get_portfolio", {})
    assert out["available"] is True
    assert out["equity"] == 940.0
    assert out["captured_at"] == "2026-10-11T01:00:00+00:00"
    assert out["positions"][0]["asset"] == "BTC"


@pytest.mark.asyncio
async def test_portfolio_surfaces_unexplained_holdings(main_db):
    await _snapshot(
        equity=1000.0,
        captured_at="2026-10-11T00:00:00+00:00",
        gaps=[
            {"asset": "BTC", "exchange_qty": 0.5, "attributed_qty": 0.4, "unexplained_qty": 0.1},
            {"asset": "ETH", "exchange_qty": 1.0, "attributed_qty": 1.0, "unexplained_qty": 0.0},
        ],
    )
    out = await _registry().call("get_portfolio", {})
    assets = [g["asset"] for g in out["unexplained_positions"]]
    assert assets == ["BTC"], "解释得清的那条不该混进来"


@pytest.mark.asyncio
async def test_market_summary_computes_from_local_bars(market_db):
    await KlineRepository().insert_rows([_bar(i) for i in range(30)])
    out = await _registry().call("get_market", {"symbol": "BTCUSDT", "bars": 30})
    assert out["available"] is True
    assert out["bars"] == 30
    assert out["last_price"] == 129.5, "第 29 根收盘 = 100+29+0.5"
    assert out["high"] == 130.0
    assert out["low"] == 99.0
    assert out["sma20"] is not None and out["rsi14"] is not None
    assert out["last_bar"].startswith("2023-11-14")


@pytest.mark.asyncio
async def test_market_isolates_symbols(market_db):
    await KlineRepository().insert_rows([_bar(i) for i in range(5)])
    out = await _registry().call("get_market", {"symbol": "ETHUSDT"})
    assert out["available"] is False, "别的 symbol 的 K 线不能被拿来充数"


@pytest.mark.asyncio
async def test_market_clamps_absurd_bar_requests(market_db):
    await KlineRepository().insert_rows([_bar(i) for i in range(5)])
    reg = _registry()
    assert (await reg.call("get_market", {"bars": 10_000}))["bars"] == 5
    assert (await reg.call("get_market", {"bars": -3}))["available"] is True
    assert (await reg.call("get_market", {"bars": "很多"}))["bars"] == 5


@pytest.mark.asyncio
async def test_risk_limits_come_from_settings():
    cfg = Settings(
        max_trade_amount=42.0,
        daily_loss_limit=77.0,
        stop_loss_pct=0.11,
        max_drawdown_pct=0.06,
        max_position_pct=0.4,
        force_close_enabled=False,
    )
    out = await _registry(cfg).call("get_risk_limits", {})
    assert out["max_trade_amount"] == 42.0
    assert out["daily_loss_limit"] == 77.0
    assert out["stop_loss_pct"] == 0.11
    assert out["max_drawdown_pct"] == 0.06
    assert out["max_position_pct"] == 0.4
    assert out["force_close_enabled"] is False


@pytest.mark.asyncio
async def test_drawdown_uses_full_history_peak(main_db):
    reg = _registry()
    await _snapshot(equity=1000.0, captured_at="2026-10-11T00:00:00+00:00")
    await _snapshot(equity=940.0, captured_at="2026-10-11T01:00:00+00:00")
    out = await reg.call("get_drawdown", {})
    assert out["available"] is True
    assert out["peak_equity"] == 1000.0
    assert out["current_equity"] == 940.0
    assert out["drawdown_pct"] == pytest.approx(0.06)
    assert out["breached"] is True, "默认上限 5%，6% 已经破线"


@pytest.mark.asyncio
async def test_recent_decisions_are_flattened_for_the_model(main_db):
    await DecisionRepository().save(
        decision_id="dec-1",
        model_used="ollama:test",
        prompt_summary="摘要",
        raw_output="{}",
        parsed_signal={"action": "BUY", "symbol": "BTCUSDT", "confidence": 0.7, "reason": "突破了"},
        goal_id="G-1",
        plan_id="P-1",
    )
    out = await _registry().call("get_recent_decisions", {})
    assert out["count"] == 1
    item = out["decisions"][0]
    assert item["action"] == "BUY"
    assert item["reason"] == "突破了"
    assert item["goal_id"] == "G-1" and item["plan_id"] == "P-1"


@pytest.mark.asyncio
async def test_recent_trades_carry_the_risk_verdict(main_db):
    await TradeRepository().save_signal(
        trade_id="trd-1",
        signal={"action": "BUY", "symbol": "BTCUSDT", "confidence": 0.6, "reason": "信号"},
        market_data={"last": 1.0},
        status="filled",
        risk_decision="approved",
        risk_reason="在额度内",
        order_type="MARKET",
    )
    out = await _registry().call("get_recent_trades", {})
    assert out["count"] == 1
    assert out["trades"][0]["risk_decision"] == "approved"
    assert out["trades"][0]["risk_reason"] == "在额度内"


@pytest.mark.asyncio
async def test_recent_risk_events_decode_detail(main_db):
    await RiskEventRepository().save(event_type="drawdown_reset", detail={"reason": "手动重置"})
    out = await _registry().call("get_recent_risk_events", {})
    assert out["count"] == 1
    assert out["events"][0]["event_type"] == "drawdown_reset"
    assert out["events"][0]["detail"] == {"reason": "手动重置"}


@pytest.mark.asyncio
async def test_corrupt_json_columns_do_not_crash_the_tool(main_db):
    """库里被人手改坏过？工具要能扛住，而不是把经理整轮打挂。"""
    row = await _snapshot(equity=1000.0, captured_at="2026-10-11T00:00:00+00:00")
    from agent.storage import database as db_mod

    factory = db_mod.get_session_factory()
    async with factory() as db:
        from agent.storage.models import EquitySnapshotLog

        obj = await db.get(EquitySnapshotLog, row.id)
        obj.positions = "{不是 json"
        await db.commit()

    out = await _registry().call("get_portfolio", {})
    assert out["available"] is True
    assert out["positions"] == []


@pytest.mark.asyncio
async def test_tools_never_write_anything(main_db, market_db):
    """跑一遍所有工具，主库里不该多出任何行。"""
    reg = _registry()
    before = {}
    factory = database.get_session_factory()
    async with factory() as db:
        from sqlalchemy import func, select

        from agent.storage.models import (
            DecisionLog,
            EquitySnapshotLog,
            OrderLog,
            RiskEvent,
            TradeLog,
        )

        for model in (TradeLog, DecisionLog, OrderLog, RiskEvent, EquitySnapshotLog):
            before[model.__tablename__] = (
                await db.execute(select(func.count()).select_from(model))
            ).scalar_one()

    for spec in reg.specs():
        await reg.call(spec.name, {"symbol": "BTCUSDT", "limit": 5, "bars": 5})

    async with factory() as db:
        for model in (TradeLog, DecisionLog, OrderLog, RiskEvent, EquitySnapshotLog):
            after = (await db.execute(select(func.count()).select_from(model))).scalar_one()
            assert after == before[model.__tablename__], f"{model.__tablename__} 被工具写过了"


def test_tool_parameters_are_valid_json_schema():
    """schema 要能被当真 —— 后面要拿它去校验模型给的参数。"""
    for spec in _registry().specs():
        assert spec.parameters["type"] == "object"
        assert isinstance(spec.parameters.get("properties", {}), dict)
        assert json.dumps(spec.schema(), ensure_ascii=False)
