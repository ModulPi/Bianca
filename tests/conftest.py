from unittest.mock import AsyncMock, patch

import pytest

from agent.config import clear_settings_cache
from agent.storage.database import close_db, init_db


@pytest.fixture(autouse=True)
def isolated_sqlite_db(monkeypatch, tmp_path):
    """每个测试使用独立 SQLite，避免 trade_logs / agent_config 状态串扰。"""
    db_file = tmp_path / "bianca.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture(autouse=True)
async def reset_db_engine():
    await init_db()
    yield
    await close_db()


@pytest.fixture(autouse=True)
def demo_trading_mode():
    demo = AsyncMock(return_value="demo")
    with patch("agent.trading.mode.get_trading_mode", demo):
        with patch("agent.api.validation_routes.get_trading_mode", demo):
            yield


@pytest.fixture(autouse=True)
def mock_position_sync():
    """避免 execute/fetch_market 在未 mock 时访问真实 Binance Demo。"""
    with patch(
        "agent.graph.execute_agent.sync_positions_from_exchange",
        AsyncMock(return_value=0),
    ):
        with patch(
            "agent.graph.supervisor.sync_positions_from_balance",
            AsyncMock(return_value=0),
        ):
            yield
