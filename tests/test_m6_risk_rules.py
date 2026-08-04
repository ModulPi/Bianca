from dataclasses import dataclass

from agent.config import Settings
from agent.risk.rules import (
    CircuitBreakerRule,
    DrawdownRule,
    InsufficientBalanceRule,
    MaxTradeAmountRule,
    MinConfidenceRule,
    PositionLimitRule,
    RiskContext,
    StopLossRule,
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
    assert len(default_rules()) == 9


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


def test_stop_loss_rejects_underwater_buy():
    @dataclass
    class Ctx(RiskContext):
        unrealized_pnl_usdt: float = -30.0

    rule = StopLossRule()
    ctx = Ctx(
        signal={"action": "BUY", "amount": 10, "confidence": 0.9},
        market_data={"last": 65000},
        settings=_settings(stop_loss_usdt=25.0),
        daily_pnl=0,
        unrealized_pnl_usdt=-30.0,
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.rule == "stop_loss"


def test_insufficient_balance_rejects_buy():
    rule = InsufficientBalanceRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 100, "confidence": 0.9},
        market_data={"last": 65000, "balance": {"free": {"USDT": 50.0, "BTC": 0}}},
        settings=_settings(),
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.rule == "insufficient_balance"


def test_position_limit_rejects_oversized_buy():
    rule = PositionLimitRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 500, "confidence": 0.9},
        market_data={
            "last": 65000,
            "balance": {"free": {"USDT": 600.0, "BTC": 0.001}},
        },
        settings=_settings(max_position_pct=0.5),
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.rule == "position_limit"


def test_drawdown_rejects():
    @dataclass
    class Ctx(RiskContext):
        daily_pnl_peak: float = 10.0

    rule = DrawdownRule()
    ctx = Ctx(
        signal={"action": "BUY", "amount": 10, "confidence": 0.9},
        market_data={"last": 65000},
        settings=_settings(max_drawdown_usdt=20.0),
        daily_pnl=-15.0,
        daily_pnl_peak=10.0,
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.rule == "drawdown"


def test_max_trade_amount_rejects():
    rule = MaxTradeAmountRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 200, "confidence": 0.9},
        market_data={"last": 65000},
        settings=_settings(max_trade_amount=50.0),
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert verdict.rule == "max_trade_amount"
