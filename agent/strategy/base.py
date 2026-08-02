from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


StrategyType = Literal["grid", "dca", "trend"]
SignalAction = Literal["BUY", "SELL", "HOLD"]


@dataclass
class StrategySignal:
    action: SignalAction
    symbol: str
    amount: float | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "amount": self.amount,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass
class StrategyEvalResult:
    signal: StrategySignal
    state: dict[str, Any] = field(default_factory=dict)
