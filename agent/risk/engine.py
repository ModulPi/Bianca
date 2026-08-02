from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent.config import Settings, get_settings
from agent.risk.rules import (
    CircuitBreakerRule,
    DailyLossRule,
    DrawdownRule,
    InsufficientBalanceRule,
    MaxTradeAmountRule,
    MinConfidenceRule,
    PositionLimitRule,
    RiskContext,
    RiskRule,
    RiskVerdict,
    default_rules,
)
from agent.storage.repository import AgentConfigRepository, TradeRepository


@dataclass
class ExtendedRiskContext(RiskContext):
    daily_pnl_peak: float = 0.0
    recent_failures: int = 0


class RiskEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        rules: list[RiskRule] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._rules = rules or default_rules()
        self._config_repo = AgentConfigRepository()
        self._trade_repo = TradeRepository()

    async def evaluate(
        self,
        signal: dict,
        market_data: dict,
    ) -> RiskVerdict:
        daily_pnl = await self._config_repo.get_daily_pnl()
        peak_key = "daily_pnl_peak"
        peak_row = await self._config_repo._get_row(peak_key)
        peak = float(peak_row.value) if peak_row else daily_pnl
        if daily_pnl > peak:
            peak = daily_pnl
            await self._config_repo._set_row(peak_key, str(peak))

        since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        recent_failures = await self._trade_repo.count_failed_since(since)

        ctx = ExtendedRiskContext(
            signal=signal,
            market_data=market_data,
            settings=self._settings,
            daily_pnl=daily_pnl,
            daily_pnl_peak=peak,
            recent_failures=recent_failures,
        )
        for rule in self._rules:
            verdict = rule.evaluate(ctx)
            if verdict is not None and not verdict.approved:
                return verdict
        return RiskVerdict(approved=True, reason="风控通过")
