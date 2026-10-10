"""每轮都跑的破线判定 —— 强制平仓的决策层（ADR-027）。

**为什么单独一层。** `RiskEngine` 只在有交易信号时才被叫到：LLM 回 HOLD 的那一轮，
`supervisor.route_after_analysis` 直接走 `log_only`，`risk` 节点根本不执行。
如果止损挂在风控规则里，那么"行情在跌但模型没想买"的那些轮次，止损一次都不会被检查
—— 而**那恰恰是止损最该生效的时候**。所以破线判定被抽出来，由 tick 独立驱动，
与 LLM 说了什么无关。

这一层是纯的：给持仓、高点、回撤，出该平谁。取数与下单由 `supervisor` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.config import Settings
from agent.portfolio.valuation import CASH_ASSETS, Position
from agent.risk.rules import StopBreach, drawdown_breached, stop_breaches


@dataclass(frozen=True)
class CloseInstruction:
    """一笔该执行的强制平仓。

    `quantity` 是**基础币数量** —— SELL 走 `amount_kind="base"`，
    与 `execute_agent._place_market_order` 的约定一致。
    """

    symbol: str
    asset: str
    quantity: float
    rule: str
    reason: str


@dataclass(frozen=True)
class MonitorResult:
    instructions: tuple[CloseInstruction, ...] = ()
    # 判不了、没做、做不到的说明。**不是装饰**：它让"这次没清仓"有据可查。
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def should_close(self) -> bool:
        return bool(self.instructions)

    def as_dict(self) -> dict:
        return {
            "instructions": [
                {
                    "symbol": i.symbol,
                    "asset": i.asset,
                    "quantity": i.quantity,
                    "rule": i.rule,
                    "reason": i.reason,
                }
                for i in self.instructions
            ],
            "notes": list(self.notes),
        }


def plan_closes(
    positions: tuple[Position, ...],
    highs: dict[str, float | None],
    drawdown_pct: float | None,
    *,
    settings: Settings,
    quote: str = "USDT",
) -> MonitorResult:
    """该平谁。

    回撤破线 → 清空**全部**非现金持仓（账户级信号，半途清一半没有依据，ADR-029）。
    止损破线 → 平掉**那一个**标的（不够的用现金凑不回来，平掉就是平掉）。

    两者同时成立时按回撤处理 —— 它的集合本来就更大，而且原因是账户级的。
    """
    notes: list[str] = []

    def _closeable(pos: Position) -> bool:
        if pos.asset in CASH_ASSETS:
            return False
        if not pos.quantity:
            return False
        # 数量为负不是"持仓"，是我们还不上的东西；不在这里处置
        return pos.quantity > 0

    if drawdown_breached(drawdown_pct, settings.max_drawdown_pct):
        targets = [p for p in positions if _closeable(p)]
        if not targets:
            notes.append("回撤已破线，但账户里没有可平的持仓（已全是现金）")
            return MonitorResult(notes=tuple(notes))
        return MonitorResult(
            instructions=tuple(
                CloseInstruction(
                    symbol=f"{p.asset}{quote}",
                    asset=p.asset,
                    quantity=p.quantity,
                    rule="drawdown",
                    reason=(
                        f"权益回撤 {drawdown_pct:.2%} 已超上限 "
                        f"{settings.max_drawdown_pct:.2%}，清空全部持仓"
                    ),
                )
                for p in targets
            ),
            notes=tuple(notes),
        )

    if drawdown_pct is None:
        notes.append("算不出权益回撤，无法判断是否需要清仓 —— 只能拦开仓，不能自己平仓")

    breaches: tuple[StopBreach, ...] = stop_breaches(
        positions, highs, settings.stop_loss_pct
    )
    if not breaches:
        return MonitorResult(notes=tuple(notes))

    return MonitorResult(
        instructions=tuple(
            CloseInstruction(
                symbol=f"{b.asset}{quote}",
                asset=b.asset,
                quantity=b.quantity,
                rule="stop_loss",
                reason=(
                    f"{b.asset} 从观测高点 {b.high:.2f} 回落 {b.drop:.2%}，"
                    f"超过止损线 {settings.stop_loss_pct:.2%}，平掉该标的"
                ),
            )
            for b in breaches
        ),
        notes=tuple(notes),
    )
