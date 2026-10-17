"""D-1：风控边界从哪来（ADR-021 的落地判据）。

阶段 C 把风控参数固化进了 `goals.risk_snapshot`，但执行层一直还在读 `settings`。
这一组用例盯的就是那条缝：**目标在跑时，改 `.env` 不该能挪动它的止损线**；
而目标快照自己缺列时，也不能拿今天的配置悄悄补上。
"""

from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from agent.config import Settings
from agent.risk.engine import PortfolioInputs, RiskEngine
from agent.risk.params import RISK_PARAM_FIELDS, RiskParams
from agent.risk.rules import MaxTradeAmountRule
from agent.storage import database

BASE = {
    "max_trade_amount": 50.0,
    "daily_loss_limit": 100.0,
    "stop_loss_pct": 0.10,
    "stop_lookback_hours": 24.0,
    "max_drawdown_pct": 0.05,
    "max_position_pct": 0.50,
    "force_close_enabled": True,
}


def _snapshot(**over) -> dict:
    return {**BASE, **over}


def _settings(**over) -> Settings:
    return Settings(**{**BASE, **over})


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


# ---------------------------------------------------------------- 构造


def test_from_settings_copies_every_frozen_field():
    """字段表与 dataclass 必须对得上 —— 漏一个，它就会永远停在默认值。"""
    params = RiskParams.from_settings(_settings())

    for name in RISK_PARAM_FIELDS:
        assert getattr(params, name) == BASE[name], name
    assert set(RISK_PARAM_FIELDS) == set(RiskParams.__dataclass_fields__)


def test_from_snapshot_round_trips():
    params = RiskParams.from_snapshot(_snapshot())

    assert asdict(params) == BASE


def test_a_missing_field_raises_instead_of_falling_back_to_config():
    """**关键**：缺列不能回落。回落等于给目标凭空加一条没人确认过的边界。"""
    broken = _snapshot()
    del broken["max_drawdown_pct"]

    with pytest.raises(ValueError) as exc:
        RiskParams.from_snapshot(broken)

    assert "max_drawdown_pct" in str(exc.value)


def test_a_null_field_is_missing_not_zero():
    """`null` 表达不了任何边界。把它当 0 会让回撤/止损闸门**永远触发**，
    把它当"没有"则会让它**永远不触发** —— 两个方向都不该悄悄发生。"""
    with pytest.raises(ValueError) as exc:
        RiskParams.from_snapshot(_snapshot(stop_loss_pct=None))

    assert "stop_loss_pct" in str(exc.value)


def test_params_are_frozen():
    params = RiskParams.from_snapshot(_snapshot())

    with pytest.raises(Exception):
        params.stop_loss_pct = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------- 规则真的读它


@pytest.mark.asyncio
async def test_the_engine_judges_with_the_given_params_not_the_settings(db, monkeypatch):
    """同一笔单：配置放行（上限 50），目标快照拦下（上限 5）。"""
    async def _inputs(settings, *, highs_provider=None):
        return PortfolioInputs(
            daily_pnl=0.0, equity=1000.0, positions=(), highs={
                "BTC": 65000.0
            }, drawdown_pct=0.0, peak_equity=1000.0
        )

    monkeypatch.setattr("agent.risk.engine.load_portfolio_inputs", _inputs)

    engine = RiskEngine(
        _settings(max_trade_amount=50.0),
        rules=[MaxTradeAmountRule()],
        params=RiskParams.from_snapshot(_snapshot(max_trade_amount=5.0)),
    )
    verdict = await engine.evaluate(
        {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT"}, {"last": 65000.0}
    )

    assert verdict.approved is False
    assert verdict.rule == "max_trade_amount"
    assert "5.00" in verdict.reason


@pytest.mark.asyncio
async def test_the_graph_risk_node_uses_the_params_carried_in_state(db, monkeypatch):
    """接线测试：`state["risk_params"]` → 风险节点 → 裁决。

    旧的手动 tick 不带这个键，按配置走；目标路径带，就必须压过配置。
    这里两个方向都钉住 —— 只验一个方向的话，"忘了传"会静默退回配置。
    """
    from agent.graph.risk_agent import run_risk_agent

    async def _inputs(settings, *, highs_provider=None):
        return PortfolioInputs(
            daily_pnl=0.0, equity=1000.0, positions=(), highs={
                "BTC": 65000.0
            }, drawdown_pct=0.0, peak_equity=1000.0
        )

    monkeypatch.setattr("agent.risk.engine.load_portfolio_inputs", _inputs)
    signal = {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT"}

    rejected = await run_risk_agent(
        {
            "llm_signal": signal,
            "market_data": {"last": 65000.0},
            "risk_params": _snapshot(max_trade_amount=5.0),
        },
        settings=_settings(max_trade_amount=50.0),
    )
    approved = await run_risk_agent(
        {
            "llm_signal": signal,
            "market_data": {"last": 65000.0},
        },
        settings=_settings(max_trade_amount=50.0),
    )

    assert rejected["status"] == "risk_rejected"
    assert rejected["risk_decision"]["rule"] == "max_trade_amount"
    assert approved["status"] == "risk_approved"


@pytest.mark.asyncio
async def test_a_broken_snapshot_in_state_fails_the_round_loudly(db, monkeypatch):
    """快照缺列时宁可这一轮炸，也不拿配置顶上 —— 静默降级比崩溃更危险。"""
    from agent.graph.risk_agent import run_risk_agent

    broken = _snapshot(max_trade_amount=5.0)
    del broken["daily_loss_limit"]

    with pytest.raises(ValueError) as exc:
        await run_risk_agent(
            {
                "llm_signal": {"action": "BUY", "amount": 1.0, "symbol": "BTCUSDT"},
                "market_data": {"last": 65000.0},
                "risk_params": broken,
            },
            settings=_settings(),
        )

    assert "daily_loss_limit" in str(exc.value)


def test_replace_keeps_the_rest_of_the_boundaries():
    """`replace()` 是测试与将来调参的常用写法，确认它只动指定那一项。"""
    params = RiskParams.from_snapshot(_snapshot())
    tighter = replace(params, stop_loss_pct=0.02)

    assert tighter.stop_loss_pct == 0.02
    assert tighter.max_drawdown_pct == params.max_drawdown_pct
