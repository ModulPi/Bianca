"""行情库的 engine / session 管理。

与 agent/storage/database.py 平行但完全独立：不同库文件、不同 Base、
不同生命周期。启用 WAL 以让读写不互相阻塞（ADR-008）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.config import get_settings
from agent.market.models import MarketBase

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_market_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(settings.market_database_url, echo=False)

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_wal(dbapi_conn, _record):  # noqa: ANN001 — SQLAlchemy 回调签名
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return _engine


def get_market_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_market_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def init_market_db() -> None:
    engine = get_market_engine()
    async with engine.begin() as conn:
        await conn.run_sync(MarketBase.metadata.create_all)


async def close_market_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def get_market_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_market_session_factory()
    async with factory() as session:
        yield session
