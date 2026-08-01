import pytest

from agent.config import Settings
from agent.risk.engine import RiskEngine
from agent.risk.rules import MaxTradeAmountRule, RiskContext, RiskVerdict


def _settings(**overrides) -> Settings:
    base = {
        "max_trade_amount": 50.0,
        "daily_loss_limit": 100.0,
    }
    base.update(overrides)
    return Settings(**base)


def test_max_trade_amount_rejects_large_buy():
    rule = MaxTradeAmountRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 80.0},
        market_data={"last": 65000},
        settings=_settings(),
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
        settings=_settings(),
    )
    assert rule.evaluate(ctx) is None


@pytest.mark.asyncio
async def test_risk_engine_daily_loss_rejects(monkeypatch):
    async def fake_pnl() -> float:
        return -150.0

    engine = RiskEngine(_settings())
    monkeypatch.setattr(engine._config_repo, "get_daily_pnl", fake_pnl)

    verdict = await engine.evaluate(
        {"action": "BUY", "amount": 10.0, "symbol": "BTCUSDT", "reason": "t"},
        {"last": 65000},
    )
    assert verdict.approved is False
    assert verdict.rule == "daily_loss"
