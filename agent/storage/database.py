from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.config import get_settings
from agent.storage.models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

_PG_DDL_PATH = Path(__file__).resolve().parent / "sql" / "002_mvp_postgres_compat.sql"


def is_sqlite_url(url: str) -> bool:
    return url.lower().startswith("sqlite")


def is_postgres_url(url: str) -> bool:
    lowered = url.lower()
    return "postgresql" in lowered or lowered.startswith("postgres")


def schema_mode() -> str:
    return "mvp" if is_postgres_url(get_settings().database_url) else "poc"


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


def _split_sql_statements(raw: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    if buf:
        statements.append("\n".join(buf))
    return statements


def _ensure_columns(sync_conn) -> None:
    """SQLite 幂等迁移：create_all 不会给已有表加列，这里补齐缺失列。"""
    insp = inspect(sync_conn)
    for table, cols in (
        ("decision_logs", ("prompt_tokens", "completion_tokens", "total_tokens")),
        ("trade_logs", ("decision_id", "strategy_id", "strategy_name", "execution_mode")),
        ("risk_events", ("related_strategy_id",)),
    ):
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for col in cols:
            if col not in existing:
                sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")


def _init_postgres_schema(sync_conn) -> None:
    if not _PG_DDL_PATH.is_file():
        raise FileNotFoundError(f"PostgreSQL DDL not found: {_PG_DDL_PATH}")
    raw = _PG_DDL_PATH.read_text(encoding="utf-8")
    for stmt in _split_sql_statements(raw):
        sync_conn.execute(text(stmt))


async def init_db() -> None:
    settings = get_settings()
    engine = get_engine()
    async with engine.begin() as conn:
        if is_postgres_url(settings.database_url):
            await conn.run_sync(_init_postgres_schema)
        else:
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
