"""破线 → 强制平仓的实际执行（ADR-027）。

这是全系统**唯一**不由 LLM 信号驱动的下单路径。它仍然走同一个
`run_execute_agent`：幂等键、先写库再提交、提交前回查、对账，一条都不少 ——
因为"紧急"不是跳过记账的理由，恰恰相反，事后最需要能查的就是这一笔。

**豁免是显式的。** 强制平仓不经过 `RiskEngine`（它就是被风控判出来的），
因此 `MaxTradeAmountRule` 对它不生效 —— 否则持仓市值大于单笔上限时根本平不掉，
"保护"就成了空话。这条豁免写进 `risk_events`，不做隐式放行。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agent.config import Settings, get_settings
from agent.portfolio.valuation import positions_to_rows
from agent.risk.engine import load_portfolio_inputs
from agent.risk.monitor import CloseInstruction, plan_closes
from agent.storage.repository import (
    RiskEventRepository,
    TradeRepository,
    decision_context,
)

logger = logging.getLogger(__name__)

# 豁免掉的规则名。写进事件里，好让事后一眼看出这笔单没走常规闸门。
EXEMPT_RULES = ("max_trade_amount", "daily_loss", "position_limit", "stop_loss", "drawdown")


async def force_close(*, settings: Settings | None = None) -> list[dict[str, Any]]:
    """巡检一轮：破线就平，没破就什么都不做。

    返回本轮实际尝试的平仓摘要（每个元素形如
    `{"asset":..., "symbol":..., "quantity":..., "rule":..., "status":...}`）。
    """
    cfg = settings or get_settings()
    inputs = await load_portfolio_inputs(cfg)

    plan = plan_closes(
        inputs.positions,
        inputs.highs,
        inputs.drawdown_pct,
        settings=cfg,
    )
    for note in plan.notes:
        logger.info("平仓巡检：%s", note)
    if not plan.should_close:
        return []

    # 一次巡检的输入是所有平仓单共用的 —— 逐条重读会让同一轮里不同的单
    # 依据不同的持仓，事后无法解释"为什么这两个单数量不一样"。
    positions = positions_to_rows(inputs.positions)
    context = decision_context(
        None,
        equity=inputs.equity,
        positions=positions,
        highs=dict(inputs.highs),
        drawdown_pct=inputs.drawdown_pct,
    )

    results: list[dict[str, Any]] = []
    for instruction in plan.instructions:
        results.append(await _close_one(instruction, cfg, context=context, positions=positions,
                                        equity=inputs.equity))
    return results


async def _close_one(
    instruction: CloseInstruction,
    settings: Settings,
    *,
    context: dict,
    positions: list[dict],
    equity: float | None,
) -> dict[str, Any]:
    """平掉一个标的。**事件先落库再下单**：崩在中间时至少要看得出我们本打算做什么。"""
    from agent.graph.execute_agent import run_execute_agent

    trade_id = str(uuid.uuid4())
    signal = {
        "action": "SELL",
        "symbol": instruction.symbol,
        "amount": instruction.quantity,
        "confidence": 1.0,
        "reason": instruction.reason,
    }

    await TradeRepository().save_signal(
        trade_id=trade_id,
        signal=signal,
        market_data={"symbol": instruction.symbol},
        status="force_close",
        risk_decision="force_close",
        risk_reason=instruction.reason,
        # 强制平仓没有行情上下文（它不看 K 线），它的"当时依据"就是**风控输入**：
        # 持仓、观测高点、回撤。这同样要能回答 R2/R3，只是答案的形状不同。
        context=context,
        equity=equity,
        positions=positions,
    )
    await RiskEventRepository().save(
        event_type=instruction.rule,
        detail={
            "asset": instruction.asset,
            "symbol": instruction.symbol,
            "quantity": instruction.quantity,
            "reason": instruction.reason,
            # 这笔单没走常规闸门，理由必须留痕
            "force_close": True,
            "exempt_rules": list(EXEMPT_RULES),
            "exempt_reason": "强制平仓是保护动作；若受单笔金额上限约束，"
            "持仓市值大于上限时将无法平掉（ADR-027）",
        },
        related_trade_id=trade_id,
    )

    logger.warning(
        "强制平仓：%s %s 数量 %s（%s）",
        instruction.symbol,
        "SELL",
        instruction.quantity,
        instruction.reason,
    )

    state = {
        "llm_signal": signal,
        "trade_log_id": trade_id,
        "order_origin": "force_close",
        "order_origin_reason": instruction.reason,
        "market_data": {"symbol": instruction.symbol},
    }
    result = await run_execute_agent(state, settings=settings)

    return {
        "asset": instruction.asset,
        "symbol": instruction.symbol,
        "quantity": instruction.quantity,
        "rule": instruction.rule,
        "status": result.get("status"),
        "message": result.get("message"),
        "trade_log_id": trade_id,
    }
