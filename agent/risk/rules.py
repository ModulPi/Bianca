from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent.config import Settings
from agent.llm.prompts import base_asset_for_symbol, normalize_symbol, resolve_worker_symbol


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

        if notional > max_usdt + 0.01:
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


class MinConfidenceRule:
    name = "min_confidence"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        signal = ctx.signal
        if signal.get("action") not in {"BUY", "SELL"}:
            return None
        confidence = float(signal.get("confidence") or 0)
        minimum = ctx.settings.min_confidence
        if confidence < minimum:
            return RiskVerdict(
                approved=False,
                reason=f"LLM 置信度 {confidence:.2f} 低于阈值 {minimum:.2f}",
                rule=self.name,
            )
        return None


class PositionLimitRule:
    name = "position_limit"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        signal = ctx.signal
        if signal.get("action") != "BUY":
            return None
        balance = ctx.market_data.get("balance") or {}
        free = balance.get("free") or {}
        last = float(ctx.market_data.get("last") or 0)
        if last <= 0:
            return None

        symbol = resolve_worker_symbol(market_data=ctx.market_data, signal=ctx.signal, settings=ctx.settings)
        base = base_asset_for_symbol(symbol)
        usdt = float(free.get("USDT") or 0)
        base_qty = float(free.get(base) or 0)
        amount = float(signal.get("amount") or 0)
        portfolio = usdt + base_qty * last
        if portfolio <= 0:
            return None
        after_base = base_qty + amount / last
        after_notional = after_base * last
        if after_notional / portfolio > ctx.settings.max_position_pct + 0.01:
            return RiskVerdict(
                approved=False,
                reason=f"买入后 {base} 仓位占比将超过 {ctx.settings.max_position_pct:.0%}",
                rule=self.name,
            )
        return None


class InsufficientBalanceRule:
    name = "insufficient_balance"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        signal = ctx.signal
        action = signal.get("action")
        if action not in {"BUY", "SELL"}:
            return None
        balance = ctx.market_data.get("balance") or {}
        free = balance.get("free") or {}
        amount = float(signal.get("amount") or 0)
        if amount <= 0:
            return None
        if action == "BUY":
            usdt = float(free.get("USDT") or 0)
            if usdt + 0.01 < amount:
                return RiskVerdict(
                    approved=False,
                    reason=f"USDT 余额 {usdt:.2f} 不足以买入 {amount:.2f}",
                    rule=self.name,
                )
        else:
            symbol = resolve_worker_symbol(
                market_data=ctx.market_data, signal=ctx.signal, settings=ctx.settings
            )
            base = base_asset_for_symbol(symbol)
            base_qty = float(free.get(base) or 0)
            if base_qty + 1e-9 < amount:
                return RiskVerdict(
                    approved=False,
                    reason=f"{base} 余额 {base_qty:.6f} 不足以卖出 {amount:.6f}",
                    rule=self.name,
                )
        return None


class StopLossRule:
    name = "stop_loss"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        if ctx.signal.get("action") != "BUY":
            return None
        unrealized = getattr(ctx, "unrealized_pnl_usdt", None)
        if unrealized is None:
            return None
        limit = ctx.settings.stop_loss_usdt
        if unrealized <= -limit:
            return RiskVerdict(
                approved=False,
                reason=f"未实现亏损 {abs(unrealized):.2f} USDT 触发止损 {limit:.2f}",
                rule=self.name,
            )
        return None


class DrawdownRule:
    name = "drawdown"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        if ctx.signal.get("action") not in {"BUY", "SELL"}:
            return None
        peak = getattr(ctx, "daily_pnl_peak", ctx.daily_pnl)
        drawdown = peak - ctx.daily_pnl
        if drawdown >= ctx.settings.max_drawdown_usdt:
            return RiskVerdict(
                approved=False,
                reason=f"当日回撤 {drawdown:.2f} USDT 超过上限 {ctx.settings.max_drawdown_usdt:.2f}",
                rule=self.name,
            )
        return None


class CircuitBreakerRule:
    name = "circuit_breaker"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        if ctx.signal.get("action") not in {"BUY", "SELL"}:
            return None
        failures = getattr(ctx, "recent_failures", 0)
        if failures >= ctx.settings.circuit_breaker_failures:
            return RiskVerdict(
                approved=False,
                reason=f"近 1 小时失败订单 {failures} 笔，触发熔断",
                rule=self.name,
            )
        return None


class TradeSymbolRule:
    name = "trade_symbol"

    def evaluate(self, ctx: RiskContext) -> RiskVerdict | None:
        if ctx.signal.get("action") not in {"BUY", "SELL"}:
            return None

        sym = normalize_symbol(
            str(ctx.signal.get("symbol") or resolve_worker_symbol(market_data=ctx.market_data, settings=ctx.settings))
        )
        allowed = {normalize_symbol(s) for s in ctx.settings.resolved_agent_symbols}
        if sym not in allowed:
            allowed_list = ", ".join(sorted(allowed))
            return RiskVerdict(
                approved=False,
                reason=f"信号交易对 {sym} 不在 AGENT_SYMBOLS 白名单 [{allowed_list}]",
                rule=self.name,
            )
        return None


def default_rules() -> list[RiskRule]:
    return [
        MaxTradeAmountRule(),
        DailyLossRule(),
        MinConfidenceRule(),
        StopLossRule(),
        PositionLimitRule(),
        InsufficientBalanceRule(),
        DrawdownRule(),
        CircuitBreakerRule(),
        TradeSymbolRule(),
    ]
