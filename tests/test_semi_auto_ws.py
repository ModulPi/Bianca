import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from agent.config import Settings, clear_settings_cache, set_effective_settings
from agent.confirmation.service import expire_pending_signals, queue_pending_signal
from agent.graph.supervisor import route_after_analysis
from agent.storage.database import close_db, init_db
from agent.storage.repository import PendingSignalRepository


@pytest.fixture
async def _db():
    await init_db()
    yield
    await close_db()


def test_route_after_analysis_semi_auto():
    clear_settings_cache()
    set_effective_settings(Settings(execution_mode="semi_auto", llm_auto_execute=True))
    try:
        result = route_after_analysis(
            {"llm_signal": {"action": "BUY", "amount": 10, "confidence": 0.9}, "llm_auto_execute": True}
        )
        assert result == "queue_pending"
    finally:
        clear_settings_cache()


@pytest.mark.asyncio
async def test_queue_pending_signal_creates_row(_db):
    state = {
        "llm_signal": {"action": "BUY", "symbol": "BTCUSDT", "amount": 10.0, "confidence": 0.9, "reason": "t"},
        "market_data": {"symbol": "BTCUSDT", "last": 65000.0, "balance": {"free": {"USDT": 1000.0}}},
        "decision_id": "dec-semi",
        "session_id": "sess-semi",
    }
    with patch("agent.confirmation.service.ws_manager.broadcast", AsyncMock()) as broadcast:
        result = await queue_pending_signal(state, session_id="sess-semi")
    assert result["status"] == "awaiting_confirmation"
    assert result.get("pending_signal_id")
    broadcast.assert_awaited_once()
    payload = broadcast.await_args.args[0]
    assert payload["type"] == "confirmation_required"

    repo = PendingSignalRepository()
    row = await repo.get_by_id(result["pending_signal_id"])
    assert row is not None
    assert row.status == "pending"


@pytest.mark.asyncio
async def test_expire_pending_signals_marks_stale_rows(_db):
    repo = PendingSignalRepository()
    expired_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    row = await repo.create(
        signal={"action": "BUY", "symbol": "BTCUSDT", "amount": 10.0, "confidence": 0.9, "reason": "t"},
        market_data={"symbol": "BTCUSDT", "last": 65000.0},
        decision_id=None,
        session_id=None,
        ttl_minutes=30,
    )
    from agent.storage.database import get_session_factory
    from agent.storage.models import PendingSignalRow

    factory = get_session_factory()
    async with factory() as db:
        db_row = await db.get(PendingSignalRow, row.id)
        assert db_row is not None
        db_row.expires_at = expired_at
        await db.commit()

    count = await expire_pending_signals()
    assert count >= 1
    updated = await repo.get_by_id(row.id)
    assert updated is not None
    assert updated.status == "expired"
