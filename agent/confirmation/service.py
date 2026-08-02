from __future__ import annotations

import json
import logging
from typing import Any

from agent.api.ws_manager import ws_manager
from agent.config import Settings, get_settings
from agent.graph.execute_agent import run_execute_agent
from agent.graph.risk_agent import run_risk_agent
from agent.graph.state import TradeState
from agent.storage.repository import PendingSignalRepository

logger = logging.getLogger(__name__)


async def queue_pending_signal(
    state: TradeState,
    *,
    settings: Settings | None = None,
    session_id: str | None = None,
    strategy_id: str | None = None,
) -> TradeState:
    cfg = settings or get_settings()
    signal = state.get("llm_signal") or {}
    market_data = state.get("market_data") or {}
    repo = PendingSignalRepository()
    row = await repo.create(
        signal=signal,
        market_data=market_data,
        decision_id=state.get("decision_id"),
        session_id=session_id,
        ttl_minutes=cfg.pending_signal_ttl_minutes,
        strategy_id=strategy_id,
    )
    payload = {
        "type": "confirmation_required",
        "pending_id": row.id,
        "signal": signal,
        "expires_at": row.expires_at,
        "session_id": session_id,
        "strategy_id": strategy_id,
    }
    await ws_manager.broadcast(payload)
    return {
        **state,
        "pending_signal_id": row.id,
        "status": "awaiting_confirmation",
        "message": f"半自动模式：等待用户确认（{row.expires_at} 前有效）",
    }


async def confirm_pending_signal(
    pending_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    repo = PendingSignalRepository()
    row = await repo.get_by_id(pending_id)
    if row is None:
        raise ValueError("待确认信号不存在")
    if row.status != "pending":
        raise ValueError(f"信号状态为 {row.status}，无法确认")
    from datetime import UTC, datetime

    if row.expires_at < datetime.now(UTC).isoformat():
        await repo.update_status(pending_id, "expired")
        raise ValueError("信号已过期")

    signal = json.loads(row.signal_json)
    market_data = json.loads(row.market_data_json)
    state: TradeState = {
        "llm_signal": signal,
        "market_data": market_data,
        "decision_id": row.decision_id,
        "llm_auto_execute": True,
    }
    state = await run_risk_agent(state, settings=cfg)
    risk = state.get("risk_decision") or {}
    if not risk.get("approved"):
        await repo.update_status(pending_id, "rejected")
        await ws_manager.broadcast(
            {"type": "confirmation_rejected", "pending_id": pending_id, "reason": risk.get("reason")}
        )
        return {"status": "risk_rejected", "state": state}

    state = await run_execute_agent(state, settings=cfg)
    await repo.update_status(pending_id, "confirmed")
    await ws_manager.broadcast(
        {
            "type": "confirmation_executed",
            "pending_id": pending_id,
            "status": state.get("status"),
            "trade_log_id": state.get("trade_log_id"),
        }
    )
    return {"status": state.get("status"), "state": state}


async def reject_pending_signal(pending_id: str) -> dict[str, str]:
    repo = PendingSignalRepository()
    row = await repo.get_by_id(pending_id)
    if row is None:
        raise ValueError("待确认信号不存在")
    if row.status != "pending":
        raise ValueError(f"信号状态为 {row.status}，无法拒绝")
    await repo.update_status(pending_id, "rejected")
    await ws_manager.broadcast({"type": "confirmation_rejected", "pending_id": pending_id, "reason": "user"})
    return {"status": "rejected", "pending_id": pending_id}


async def expire_pending_signals() -> int:
    repo = PendingSignalRepository()
    count = await repo.expire_stale()
    if count:
        logger.info("Expired %s pending signals", count)
    return count
