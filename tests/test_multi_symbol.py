from agent.config import Settings
from agent.llm.prompts import resolve_worker_symbol
from agent.risk.rules import InsufficientBalanceRule, PositionLimitRule, RiskContext


def test_resolve_worker_symbol_prefers_market_data():
    sym = resolve_worker_symbol(
        market_data={"symbol": "ETHUSDT"},
        settings=Settings(trade_symbol="BTCUSDT"),
    )
    assert sym == "ETHUSDT"


def test_position_limit_uses_worker_symbol():
    rule = PositionLimitRule()
    ctx = RiskContext(
        signal={"action": "BUY", "amount": 50, "symbol": "ETHUSDT", "confidence": 0.9},
        market_data={
            "symbol": "ETHUSDT",
            "last": 3000,
            "balance": {"free": {"USDT": 200.0, "ETH": 0}},
        },
        settings=Settings(
            trade_symbol="BTCUSDT",
            agent_symbols="BTCUSDT,ETHUSDT",
            max_position_pct=0.5,
            max_trade_amount=100.0,
        ),
    )
    assert rule.evaluate(ctx) is None


def test_insufficient_balance_uses_worker_base_asset():
    rule = InsufficientBalanceRule()
    ctx = RiskContext(
        signal={"action": "SELL", "amount": 1.0, "symbol": "ETHUSDT", "confidence": 0.9},
        market_data={
            "symbol": "ETHUSDT",
            "last": 3000,
            "balance": {"free": {"USDT": 100.0, "ETH": 0.1}},
        },
        settings=Settings(trade_symbol="BTCUSDT", agent_symbols="BTCUSDT,ETHUSDT"),
    )
    verdict = rule.evaluate(ctx)
    assert verdict is not None
    assert "ETH" in verdict.reason
