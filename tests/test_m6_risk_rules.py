from dataclasses import dataclass

from agent.config import Settings
from agent.risk.rules import (
    CircuitBreakerRule,
    MinConfidenceRule,
    RiskContext,
    TradeSymbolRule,
    default_rules,
)


def _settings(**overrides) -> Settings:
    base = {
        "max_trade_amount": 50.0,
        "daily_loss_limit": 100.0,
        "min_confidence": 0.6,
        "max_position_pct": 0.8,
        "max_drawdown_usdt": 50.0,
        "circuit_breaker_failures": 3,
        "trade_symbol": "BTCUSDT",
    }
    base.update(overrides)
    return Settings(**base)


def test_default_rules_count():
    assert len(default_rules()) == 8


def test_min_confidence_rejects():
    rule = MinConfidenceRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 10, "confidence": 0.3},
        market_data={"last": 65000},
        settings=_settings(),
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.rule == "min_confidence"


def test_trade_symbol_rejects_mismatch():
    rule = TradeSymbolRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 10, "symbol": "ETHUSDT", "confidence": 0.9},
        market_data={"last": 3000},
        settings=_settings(),
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.rule == "trade_symbol"


def test_circuit_breaker_trips():
    @dataclass
    class Ctx(RiskContext):
        recent_failures: int = 5

    rule = CircuitBreakerRule()
    ctx = Ctx(
        signal={"action": "BUY", "amount": 10, "confidence": 0.9},
        market_data={"last": 65000},
        settings=_settings(),
        daily_pnl=0,
        recent_failures=5,
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.rule == "circuit_breaker"
