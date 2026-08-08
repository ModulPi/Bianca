from __future__ import annotations

from typing import Any

from agent.checkpoint.saver import checkpointer_context
from agent.config import Settings, get_settings
from agent.graph.supervisor import build_trade_graph
from agent.storage.database import is_postgres_url


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
        "orchestrator_plan",
        "agent_signals",
        "merge_meta",
        "analysis_signal",
        "strategy_signal",
        "chat_directives",
        "symbol",
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
    cfg = settings or get_settings()
    if is_postgres_url(cfg.database_url):
        from sqlalchemy import text

        from agent.storage.database import get_session_factory

        sql = text(
            """
            SELECT thread_id, COUNT(*) AS checkpoint_count, MAX(checkpoint_id) AS latest_checkpoint_id
            FROM checkpoints
            GROUP BY thread_id
            ORDER BY latest_checkpoint_id DESC
            LIMIT :limit
            """
        )
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(sql, {"limit": limit})
            rows = result.fetchall()
        return [
            {
                "thread_id": row[0],
                "checkpoint_count": row[1],
                "latest_checkpoint_id": row[2],
            }
            for row in rows
        ]

    import aiosqlite

    path = cfg.data_dir / "checkpoints.sqlite"
    if not path.exists():
        return []

    async with aiosqlite.connect(str(path)) as db:
        cursor = await db.execute(
            """
            SELECT thread_id, COUNT(*) AS checkpoint_count, MAX(checkpoint_id) AS latest_checkpoint_id
            FROM checkpoints
            GROUP BY thread_id
            ORDER BY latest_checkpoint_id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()

    return [
        {
            "thread_id": row[0],
            "checkpoint_count": row[1],
            "latest_checkpoint_id": row[2],
        }
        for row in rows
    ]


async def get_thread_history(
    thread_id: str,
    *,
    limit: int = 20,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    cfg = settings or get_settings()
    if not is_postgres_url(cfg.database_url):
        path = cfg.data_dir / "checkpoints.sqlite"
        if not path.exists():
            return []

    graph = build_trade_graph()
    config = {"configurable": {"thread_id": thread_id}}
    history: list[dict[str, Any]] = []

    async with checkpointer_context(settings=cfg) as checkpointer:
        app = graph.compile(checkpointer=checkpointer)
        async for snapshot in app.aget_state_history(config, limit=limit):
            history.append(_serialize_snapshot(snapshot))

    return history
