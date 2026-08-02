from collections.abc import AsyncGenerator

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.config import get_settings
from agent.storage.models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(settings.database_url, echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


def _ensure_columns(sync_conn) -> None:
    """SQLite 幂等迁移：create_all 不会给已有表加列，这里补齐缺失列。"""
    insp = inspect(sync_conn)
    for table, cols in (
        ("decision_logs", ("prompt_tokens", "completion_tokens", "total_tokens")),
        ("trade_logs", ("decision_id",)),
    ):
        existing = {c["name"] for c in insp.get_columns(table)}
        for col in cols:
            if col not in existing:
                sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col}")


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_columns)


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
