from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.config import get_settings
from agent.storage.models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def is_sqlite_url(url: str) -> bool:
    return url.lower().startswith("sqlite")


def is_postgres_url(url: str) -> bool:
    lowered = url.lower()
    return "postgresql" in lowered or lowered.startswith("postgres")


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"echo": False}
        if is_postgres_url(settings.database_url):
            kwargs["pool_pre_ping"] = True
        _engine = create_async_engine(settings.database_url, **kwargs)
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


def _init_timescale_extras(sync_conn) -> None:
    """PostgreSQL + TimescaleDB：预建 klines hypertable（ORM 未覆盖部分）。"""
    sync_conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
    sync_conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS klines (
                time        TIMESTAMPTZ NOT NULL,
                symbol      TEXT NOT NULL,
                interval    TEXT NOT NULL DEFAULT '1m',
                open        DOUBLE PRECISION NOT NULL,
                high        DOUBLE PRECISION NOT NULL,
                low         DOUBLE PRECISION NOT NULL,
                close       DOUBLE PRECISION NOT NULL,
                volume      DOUBLE PRECISION NOT NULL DEFAULT 0
            )
            """
        )
    )
    sync_conn.execute(
        text("SELECT create_hypertable('klines', 'time', if_not_exists => TRUE)")
    )


async def init_db() -> None:
    settings = get_settings()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if is_sqlite_url(settings.database_url):
            await conn.run_sync(_ensure_columns)
        elif is_postgres_url(settings.database_url):
            await conn.run_sync(_init_timescale_extras)


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
