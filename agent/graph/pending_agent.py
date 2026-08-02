from __future__ import annotations

from agent.config import get_settings
from agent.confirmation.service import queue_pending_signal
from agent.graph.state import TradeState


async def run_pending_agent(state: TradeState) -> TradeState:
    settings = get_settings()
    return await queue_pending_signal(
        state,
        settings=settings,
        session_id=state.get("session_id"),
    )
