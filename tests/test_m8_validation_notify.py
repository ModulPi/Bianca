import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import clear_settings_cache
from agent.main import app
from agent.storage.database import close_db, init_db
from agent.storage.repository import PaperValidationRepository
from agent.validation.paper_gate import evaluate_validation, record_session_for_validation


@pytest.fixture
async def client():
    clear_settings_cache()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()
    clear_settings_cache()


def _sample_summary(*, hours_span: float = 25.0, loop_closed: bool = True) -> dict:
    started = "2026-08-01T00:00:00+00:00"
    ended = "2026-08-02T01:00:00+00:00" if hours_span >= 25 else "2026-08-01T02:00:00+00:00"
    return {
        "session_id": str(uuid.uuid4()),
        "started_at": started,
        "ended_at": ended,
        "agent": {"tick_count": 5, "trading_style": "aggressive", "last_status": "ok"},
        "usage": {"llm_calls": 3, "total_tokens": 100, "prompt_tokens": 60, "completion_tokens": 40},
        "trades": {
            "buy_filled": 1,
            "sell_filled": 1,
            "failed": 0,
            "signal_only": 0,
            "loop_closed": loop_closed,
        },
        "pnl": {"realized_usdt": 1.0, "cash_flow_usdt": 0, "unrealized_usdt": 0, "total_usdt": 1.0},
        "positions": {},
        "highlights": [],
    }


@pytest.mark.asyncio
async def test_validation_passes_after_requirements():
    clear_settings_cache()
    await init_db()
    repo = PaperValidationRepository()
    await repo.reset()
    summary = _sample_summary(hours_span=25.0, loop_closed=True)
    with patch("agent.validation.paper_gate.get_settings") as mock_settings:
        mock_settings.return_value.paper_validation_min_hours = 24.0
        mock_settings.return_value.paper_validation_require_loop = True
        result = await record_session_for_validation(summary, settings=mock_settings.return_value)
    assert result["can_enable_live"] is True
    assert result["status"] == "passed"
    await close_db()


@pytest.mark.asyncio
async def test_validation_api_status(client):
    resp = await client.get("/api/v1/validation/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trading_mode"] in {"demo", "live"}
    assert "metrics" in body


@pytest.mark.asyncio
async def test_trading_mode_live_blocked_without_validation(client):
    await client.post("/api/v1/validation/reset")
    resp = await client.post("/api/v1/trading/mode", json={"mode": "live"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_notify_test_requires_telegram(client):
    resp = await client.post("/api/v1/notify/test")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_futures_status_stub(client):
    resp = await client.get("/api/v1/futures/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_telegram_send_mocked():
    with patch("agent.notify.telegram.httpx.AsyncClient") as mock_client_cls:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        from agent.config import Settings
        from agent.notify.telegram import send_telegram

        cfg = Settings(
            telegram_bot_token="token",
            telegram_chat_id="123",
        )
        ok = await send_telegram("hello", settings=cfg)
        assert ok is True


@pytest.mark.asyncio
async def test_evaluate_validation_reasons():
    clear_settings_cache()
    await init_db()
    repo = PaperValidationRepository()
    await repo.reset()
    result = await evaluate_validation()
    assert result["can_enable_live"] is False
    assert len(result["reasons"]) >= 1
    await close_db()
