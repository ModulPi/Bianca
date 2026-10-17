from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from agent.config import Settings, get_settings
from agent.graph.state import TradeState
from agent.portfolio.valuation import positions_to_rows
from agent.risk.engine import RiskEngine
from agent.risk.params import RiskParams
from agent.storage.repository import (
    RiskEventRepository,
    TradeRepository,
    decision_context,
)


async def run_risk_agent(state: TradeState, *, settings: Settings | None = None) -> TradeState:
    cfg = settings or get_settings()
    signal = state.get("llm_signal") or {}
    market_data = state.get("market_data") or {}

    # 目标在跑就用它确认时那份边界；没有才按当前配置（旧的手动 tick）。
    # `from_snapshot` 缺列会抛 —— 那一轮宁可失败，也不拿 `.env` 替目标补规则。
    raw_params = state.get("risk_params")
    params = RiskParams.from_snapshot(raw_params) if raw_params else RiskParams.from_settings(cfg)

    engine = RiskEngine(cfg, params=params)
    # 裁决**连同它的输入**取回来：那份 RiskContext 就是 R3 的答案（ADR-019）。
    # 记它，而不是事后再读一次库 —— 再读的可能已经不是裁决时那一份了。
    verdict, ctx = await engine.evaluate_detailed(signal, market_data)

    trade_repo = TradeRepository()
    risk_repo = RiskEventRepository()
    trade_id = state.get("trade_log_id") or str(uuid.uuid4())

    risk_decision = "approved" if verdict.approved else "rejected"
    positions = positions_to_rows(ctx.positions)
    await trade_repo.save_signal(
        trade_id=trade_id,
        signal=signal,
        market_data=market_data,
        risk_decision=risk_decision,
        risk_reason=None if verdict.approved else verdict.reason,
        status="failed" if not verdict.approved else "submitted",
        decision_id=state.get("decision_id"),
        context=decision_context(
            market_data,
            equity=ctx.equity,
            positions=positions,
            highs=dict(ctx.highs),
            drawdown_pct=ctx.drawdown_pct,
        ),
        equity=ctx.equity,
        positions=positions,
        goal_id=state.get("goal_id"),
        plan_id=state.get("plan_id"),
    )

    if not verdict.approved and verdict.rule:
        await risk_repo.save(
            event_type=verdict.rule,
            detail={"reason": verdict.reason, "signal": signal},
            related_trade_id=trade_id,
        )

    return {
        **state,
        "trade_log_id": trade_id,
        "risk_decision": {
            "approved": verdict.approved,
            "reason": verdict.reason,
            "rule": verdict.rule,
        },
        "status": "risk_rejected" if not verdict.approved else "risk_approved",
        "message": verdict.reason,
    }
