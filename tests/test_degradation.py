import pytest
from unittest.mock import AsyncMock, patch

from backend.config import Settings, clear_settings_cache, set_effective_settings
from backend.application.degradation import clear_degradation, get_effective_execution_mode, is_degraded, record_tick_failure
from backend.infrastructure.storage.database import close_db, init_db


@pytest.fixture
async def _db():
    await init_db()
    yield
    await close_db()


@pytest.mark.asyncio
async def test_auto_degrade_switches_effective_mode(_db):
    clear_settings_cache()
    set_effective_settings(
        Settings(
            execution_mode="auto",
            auto_degrade_enabled=True,
            auto_degrade_failures=2,
        )
    )
    try:
        assert await get_effective_execution_mode() == "auto"
        with patch("backend.infrastructure.notify.email.notify_all", AsyncMock(return_value={"telegram": False, "email": False})):
            await record_tick_failure("BTCUSDT", "err1")
            assert not await is_degraded()
            await record_tick_failure("BTCUSDT", "err2")
            assert await is_degraded()
        assert await get_effective_execution_mode() == "semi_auto"
        await clear_degradation()
        assert not await is_degraded()
    finally:
        clear_settings_cache()
