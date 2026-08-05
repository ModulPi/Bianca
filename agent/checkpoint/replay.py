from __future__ import annotations

from typing import Any

from agent.checkpoint.store import checkpoint_saver, list_checkpoint_threads
from agent.config import Settings, get_settings
from agent.graph.supervisor import build_trade_graph


def _serialize_state(values: dict[str, Any] | None) -> dict[str, Any]:
    if not values:
        return {}
    keys = (
        "status",
        "message",
        "decision_id",
        "trade_log_id",
        "llm_auto_execute",
        "llm_signal",
        "risk_decision",
        "order_result",
        "market_data",
    )
    out: dict[str, Any] = {}
    for key in keys:
        if key in values and values[key] is not None:
            out[key] = values[key]
    return out


def _serialize_snapshot(snapshot: Any) -> dict[str, Any]:
    config = snapshot.config or {}
    configurable = config.get("configurable") or {}
    metadata = snapshot.metadata or {}
    return {
        "checkpoint_id": configurable.get("checkpoint_id"),
        "thread_id": configurable.get("thread_id"),
        "created_at": getattr(snapshot, "created_at", None),
        "next_nodes": list(snapshot.next or ()),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "state": _serialize_state(getattr(snapshot, "values", None)),
    }


async def list_threads(*, limit: int = 50, settings: Settings | None = None) -> list[dict[str, Any]]:
    return await list_checkpoint_threads(limit=limit, settings=settings)


async def get_thread_history(
    thread_id: str,
    *,
    limit: int = 20,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    cfg = settings or get_settings()
    graph = build_trade_graph()
    config = {"configurable": {"thread_id": thread_id}}
    history: list[dict[str, Any]] = []

    async with checkpoint_saver(cfg) as checkpointer:
        app = graph.compile(checkpointer=checkpointer)
        async for snapshot in app.aget_state_history(config, limit=limit):
            history.append(_serialize_snapshot(snapshot))

    return history
