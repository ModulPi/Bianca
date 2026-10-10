from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from agent.config import Settings, get_settings
from agent.portfolio.klines import fetch_recent_highs
from agent.portfolio.pnl import daily_pnl as compute_daily_pnl
from agent.portfolio.pnl import drawdown as compute_drawdown
from agent.portfolio.valuation import CASH_ASSETS, Position, positions_from_rows
from agent.risk.rules import (
    DailyLossRule,
    DrawdownRule,
    MaxTradeAmountRule,
    PositionLimitRule,
    RiskContext,
    RiskRule,
    RiskVerdict,
    TrailingStopRule,
)
from agent.storage.repository import EquityRepository

logger = logging.getLogger(__name__)

# 资产列表 → 资产 → 近期高点。抽成参数是为了让测试能换掉这条**唯一出网**的取数。
HighsProvider = Callable[[Iterable[str]], Awaitable[dict[str, float | None]]]


class RiskEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        rules: list[RiskRule] | None = None,
        highs_provider: HighsProvider | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._rules = rules or [
            MaxTradeAmountRule(),
            DailyLossRule(),
            PositionLimitRule(),
            TrailingStopRule(),
            DrawdownRule(),
        ]
        self._highs_provider = highs_provider or self._fetch_highs

    async def evaluate(
        self,
        signal: dict,
        market_data: dict,
    ) -> RiskVerdict:
        verdict, _ = await self.evaluate_detailed(signal, market_data)
        return verdict

    async def evaluate_detailed(
        self,
        signal: dict,
        market_data: dict,
    ) -> tuple[RiskVerdict, RiskContext]:
        """裁决**连同它的输入一起**返回（ADR-019 R3）。

        归因要的是"当时手里有什么"，而那个答案就是 `RiskContext` —— 拿它去落库，
        而不是事后再读一次库。再读一次的话，记下来的可能已经不是裁决时的那份了，
        而且那种偏差没有任何地方会报出来。
        """
        ctx = await self._build_context(signal, market_data)
        for rule in self._rules:
            verdict = rule.evaluate(ctx)
            if verdict is not None and not verdict.approved:
                return verdict, ctx
        return RiskVerdict(approved=True, reason="风控通过"), ctx

    async def _build_context(self, signal: dict, market_data: dict) -> RiskContext:
        """把规则的输入凑齐。**每一段取不到都是 None，不是默认值。**

        权益、持仓、日盈亏、回撤全部取自**最新快照** —— 它每轮 tick 开头刷新一次
        （`supervisor`），比实时余额可复现：事后能拿同一张快照复算当时的裁决。
        唯独"近期高点"要出网取实盘 K 线，因为那个数不在快照里。
        """
        inputs = await load_portfolio_inputs(self._settings, highs_provider=self._highs_provider)
        return RiskContext(
            signal=signal,
            market_data=market_data,
            settings=self._settings,
            daily_pnl=inputs.daily_pnl,
            equity=inputs.equity,
            positions=inputs.positions,
            highs=inputs.highs,
            drawdown_pct=inputs.drawdown_pct,
            peak_equity=inputs.peak_equity,
            drawdown_since=inputs.drawdown_since,
        )

    async def _fetch_highs(self, assets: Iterable[str]) -> dict[str, float | None]:
        return await _fetch_highs(assets, self._settings)


@dataclass(frozen=True)
class PortfolioInputs:
    """一次评估/一次巡检所需的全部账户侧输入。**每一段都可能是 None。**

    `supervisor` 的清仓巡检与 `RiskEngine` 的裁决共用它 —— 两条路径必须看同一份
    数字，否则会出现"风控说破线了、巡检说没破"这种自相矛盾。
    """

    daily_pnl: float | None = None
    equity: float | None = None
    positions: tuple[Position, ...] = ()
    highs: dict[str, float | None] = field(default_factory=dict)
    drawdown_pct: float | None = None
    peak_equity: float | None = None
    drawdown_since: str | None = None


async def load_portfolio_inputs(
    settings: Settings,
    *,
    highs_provider: HighsProvider | None = None,
) -> PortfolioInputs:
    daily = await compute_daily_pnl()
    equity, positions = await _latest_holdings()
    dd = await compute_drawdown()
    provider = highs_provider or (lambda assets: _fetch_highs(assets, settings))
    highs = await provider(_stop_assets(positions))
    return PortfolioInputs(
        daily_pnl=daily.pnl,
        equity=equity,
        positions=positions,
        highs=highs,
        drawdown_pct=dd.pct,
        peak_equity=dd.peak_equity,
        drawdown_since=dd.since,
    )


async def _fetch_highs(
    assets: Iterable[str], settings: Settings
) -> dict[str, float | None]:
    wanted = list(assets)
    if not wanted:  # 没持仓就别出网
        return {}
    return await fetch_recent_highs(wanted, settings=settings)


def _stop_assets(positions: Iterable[Position]) -> list[str]:
    """需要取"近期高点"的资产：非现金、且数量不为零的持仓。"""
    return [p.asset for p in positions if p.asset not in CASH_ASSETS and p.quantity]


async def _latest_holdings() -> tuple[float | None, tuple[Position, ...]]:
    """从最新权益快照里取权益与持仓。没有快照就是 (None, ())。

    解析失败按"没有持仓"处理但不静默 —— 少一个持仓会让仓位上限**看起来更宽松**，
    所以这里要留一条日志，别让它在库里烂掉。
    """
    row = await EquityRepository().latest()
    if row is None:
        return None, ()
    try:
        rows = json.loads(row.positions or "[]")
    except (TypeError, ValueError):
        logger.warning("权益快照 %s 的 positions 解析失败，本轮按无持仓处理", row.id)
        return row.equity, ()
    return row.equity, positions_from_rows(rows)
