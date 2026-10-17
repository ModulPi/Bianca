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
    """SQLite 幂等迁移：create_all 不会给已有表加列，这里补齐缺失列。

    **线上库 `data/bianca.db` 里已经有数据**，所以加列只能走 ALTER TABLE ——
    删表重建会把历史记录一起抹掉，那正是这套东西存在的理由。

    SQLite 允许不带类型地 ADD COLUMN（新列是无类型/BLOB 亲和），存什么都行，
    因此这里只写列名。旧行得到 NULL，正好表示"这条记录当时还没记这个"。
    """
    insp = inspect(sync_conn)
    for table, cols in (
        ("decision_logs", (
            "prompt_tokens", "completion_tokens", "total_tokens",
            "prompt", "prompt_variant", "market_data", "goal_id", "plan_id",
            "plan_step",
        )),
        ("trade_logs", (
            "decision_id",
            "context", "equity", "positions", "goal_id", "plan_id",
        )),
        # `goals` 是 C-1 才建的表，但"已经跑过一次 C-1 的库"是存在的 ——
        # 那时它没有 `target_equity`。create_all 不会给已有表补列，所以列在这里。
        ("goals", ("target_equity",)),
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
