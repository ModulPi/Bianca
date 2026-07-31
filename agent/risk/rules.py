from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent.config import Settings


@dataclass
class RiskContext:
    signal: dict[str, Any]
    market_data: dict[str, Any]
    settings: Settings
    daily_pnl: float = 0.0


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
    rule: str | None = None


class RiskRule(Protocol):
    name: str

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        """Return RiskVerdict if rule triggers rejection; None if passed."""


class MaxTradeAmountRule:
    name = "max_trade_amount"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        signal = ctx.signal
        action = signal.get("action", "HOLD")
        if action not in {"BUY", "SELL"}:
            return None

        amount = signal.get("amount")
        if amount is None or float(amount) <= 0:
            return RiskVerdict(
                approved=False,
                reason="可执行信号缺少有效 amount",
                rule=self.name,
            )

        max_usdt = ctx.settings.max_trade_amount
        notional = float(amount)

        if action == "SELL":
            price = ctx.market_data.get("last") or ctx.market_data.get("bid")
            if price:
                notional = float(amount) * float(price)

        if notional > max_usdt:
            return RiskVerdict(
                approved=False,
                reason=f"单笔名义金额 {notional:.2f} USDT 超过上限 {max_usdt:.2f}",
                rule=self.name,
            )
        return None


class DailyLossRule:
    name = "daily_loss"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        limit = ctx.settings.daily_loss_limit
        if ctx.daily_pnl <= -limit:
            return RiskVerdict(
                approved=False,
                reason=f"日亏损 {abs(ctx.daily_pnl):.2f} USDT 已达熔断阈值 {limit:.2f}",
                rule=self.name,
            )
        return None
