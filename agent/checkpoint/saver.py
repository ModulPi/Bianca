from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from agent.config import Settings, get_settings
from agent.storage.database import is_postgres_url


def postgres_conn_string(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    url = cfg.database_url
    return url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")


@asynccontextmanager
async def checkpointer_context(*, settings: Settings | None = None) -> AsyncIterator:
    """Yield LangGraph AsyncSqliteSaver or AsyncPostgresSaver based on DATABASE_URL."""
    cfg = settings or get_settings()
    if is_postgres_url(cfg.database_url):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        conn = postgres_conn_string(cfg)
        async with AsyncPostgresSaver.from_conn_string(conn) as saver:
            await saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.data_dir / "checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            yield saver
