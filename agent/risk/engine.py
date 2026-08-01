from __future__ import annotations

from agent.config import Settings, get_settings
from agent.risk.rules import DailyLossRule, MaxTradeAmountRule, RiskContext, RiskRule, RiskVerdict
from agent.storage.repository import AgentConfigRepository


class RiskEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        rules: list[RiskRule] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._rules = rules or [MaxTradeAmountRule(), DailyLossRule()]
        self._config_repo = AgentConfigRepository()

    async def evaluate(
        self,
        signal: dict,
        market_data: dict,
    ) -> RiskVerdict:
        daily_pnl = await self._config_repo.get_daily_pnl()
        ctx = RiskContext(
            signal=signal,
            market_data=market_data,
            settings=self._settings,
            daily_pnl=daily_pnl,
        )
        for rule in self._rules:
            verdict = rule.evaluate(ctx)
            if verdict is not None and not verdict.approved:
                return verdict
        return RiskVerdict(approved=True, reason="风控通过")
