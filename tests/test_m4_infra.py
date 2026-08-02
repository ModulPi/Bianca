import pytest

from agent.cache.redis_client import cache_get, cache_set, clear_active_session, set_active_session
from agent.config import Settings, clear_settings_cache
from agent.storage.database import is_postgres_url, is_sqlite_url


def test_database_url_helpers():
    assert is_sqlite_url("sqlite+aiosqlite:///./data/bianca.db")
    assert is_postgres_url("postgresql+asyncpg://u:p@localhost/bianca")
    assert not is_sqlite_url("postgresql+asyncpg://localhost/x")


@pytest.mark.asyncio
async def test_memory_cache_fallback():
    clear_settings_cache()
    await cache_set("test:key", "value")
    assert await cache_get("test:key") == "value"


@pytest.mark.asyncio
async def test_session_cache_helpers():
    await set_active_session("sess-1", "2026-08-02T00:00:00+00:00")
    from agent.cache.redis_client import get_active_session

    assert await get_active_session("sess-1") == "2026-08-02T00:00:00+00:00"
    await clear_active_session("sess-1")
    assert await get_active_session("sess-1") is None


def test_settings_database_backend():
    clear_settings_cache()
    cfg = Settings(database_url="sqlite+aiosqlite:///./data/bianca.db")
    assert cfg.database_backend == "sqlite"
    cfg2 = Settings(database_url="postgresql+asyncpg://localhost/bianca")
    assert cfg2.database_backend == "postgresql"
