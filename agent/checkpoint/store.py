from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text

from agent.config import Settings, get_settings
from agent.storage.database import get_session_factory, is_postgres_url, is_sqlite_url

_setup_keys: set[str] = set()


def postgres_checkpointer_dsn(database_url: str) -> str:
    """SQLAlchemy async URL → psycopg/libpq DSN for LangGraph AsyncPostgresSaver."""
    url = database_url.strip()
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


@asynccontextmanager
async def checkpoint_saver(settings: Settings | None = None) -> AsyncIterator[Any]:
    """SQLite（PoC）或 PostgreSQL（M4）Checkpointer。"""
    cfg = settings or get_settings()
    if is_postgres_url(cfg.database_url):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        dsn = postgres_checkpointer_dsn(cfg.database_url)
        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            if dsn not in _setup_keys:
                await saver.setup()
                _setup_keys.add(dsn)
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        path = str(cfg.data_dir / "checkpoints.sqlite")
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            yield saver


def checkpointer_backend(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    return "postgresql" if is_postgres_url(cfg.database_url) else "sqlite"


async def list_checkpoint_threads(*, limit: int = 50, settings: Settings | None = None) -> list[dict[str, Any]]:
    cfg = settings or get_settings()
    if is_postgres_url(cfg.database_url):
        stmt = text(
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
            result = await db.execute(stmt, {"limit": limit})
            rows = result.all()
        return [
            {
                "thread_id": row[0],
                "checkpoint_count": row[1],
                "latest_checkpoint_id": row[2],
            }
            for row in rows
        ]

    if not is_sqlite_url(cfg.database_url):
        return []

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
