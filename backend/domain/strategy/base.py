from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


StrategyType = Literal["grid", "dca", "trend"]
SignalAction = Literal["BUY", "SELL", "HOLD"]
StrategyMarket = Literal["spot", "futures_u", "futures_coin"]


@dataclass
class StrategySignal:
    action: SignalAction
    symbol: str
    amount: float | None
    confidence: float
    reason: str
    market: StrategyMarket = "spot"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "amount": self.amount,
            "confidence": self.confidence,
            "reason": self.reason,
            "market": self.market,
        }


def with_market(signal: StrategySignal, market: str) -> StrategySignal:
    normalized: StrategyMarket = "spot"
    if market in {"spot", "futures_u", "futures_coin"}:
        normalized = market  # type: ignore[assignment]
    return StrategySignal(
        action=signal.action,
        symbol=signal.symbol,
        amount=signal.amount,
        confidence=signal.confidence,
        reason=signal.reason,
        market=normalized,
    )


@dataclass
class StrategyEvalResult:
    signal: StrategySignal
    state: dict[str, Any] = field(default_factory=dict)
