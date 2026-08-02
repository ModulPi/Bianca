from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from agent.config import Settings, get_settings
from agent.graph.state import TradeState
from agent.risk.engine import RiskEngine
from agent.storage.repository import RiskEventRepository, TradeRepository


async def run_risk_agent(state: TradeState, *, settings: Settings | None = None) -> TradeState:
    cfg = settings or get_settings()
    signal = state.get("llm_signal") or {}
    market_data = state.get("market_data") or {}

    engine = RiskEngine(cfg)
    verdict = await engine.evaluate(signal, market_data)

    trade_repo = TradeRepository()
    risk_repo = RiskEventRepository()
    trade_id = str(uuid.uuid4())

    risk_decision = "approved" if verdict.approved else "rejected"
    await trade_repo.save_signal(
        trade_id=trade_id,
        signal=signal,
        market_data=market_data,
        risk_decision=risk_decision,
        risk_reason=None if verdict.approved else verdict.reason,
        status="failed" if not verdict.approved else "submitted",
        decision_id=state.get("decision_id"),
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
