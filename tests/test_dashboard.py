import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from agent.config import clear_settings_cache
from agent.dashboard.cache import clear_snapshot_cache
from agent.dashboard.etag import clear_snapshot_store
from agent.dashboard.invalidate import invalidate_dashboard_snapshot
from agent.dashboard.snapshot import build_dashboard_snapshot
from agent.main import app
from agent.storage.database import close_db, init_db


@pytest.fixture
async def client():
    clear_settings_cache()
    clear_snapshot_cache()
    clear_snapshot_store()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()
    clear_settings_cache()
    clear_snapshot_cache()
    clear_snapshot_store()


@pytest.mark.asyncio
async def test_dashboard_snapshot_shape(client):
    resp = await client.get("/api/v1/dashboard/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert "agent" in body
    assert "trading_mode" in body
    assert "validation" in body
    assert "health" in body
    assert "usage" in body
    assert "positions" in body
    assert "tickers" in body
    assert "open_trades" in body
    assert "recent_filled" in body
    assert "pending_signals" in body
    assert "risk_events" in body
    assert "worker_token_usage" in body
    assert "generated_at" in body
    assert body["agent"]["running"] is False


@pytest.mark.asyncio
async def test_exchange_tickers_without_binance(client):
    resp = await client.get("/api/v1/exchange/tickers?symbols=BTCUSDT,ETHUSDT")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_snapshot_health_cache(client):
    calls = {"n": 0}

    async def counted_health():
        calls["n"] += 1
        from agent.api.health_service import build_health_response

        return await build_health_response()

    with patch(
        "agent.dashboard.snapshot.build_health_response",
        new=AsyncMock(side_effect=counted_health),
    ):
        clear_snapshot_cache()
        r1 = await client.get("/api/v1/dashboard/snapshot")
        r2 = await client.get("/api/v1/dashboard/snapshot")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert calls["n"] == 1


@pytest.mark.asyncio
async def test_snapshot_etag_and_not_modified(client):
    r1 = await client.get("/api/v1/dashboard/snapshot")
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag
    assert r1.headers.get("cache-control") == "private, max-age=0, must-revalidate"

    r2 = await client.get("/api/v1/dashboard/snapshot", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.headers.get("etag") == etag
    assert r2.content in (b"", None) or len(r2.content) == 0


@pytest.mark.asyncio
async def test_snapshot_fast_not_modified(client):
    r1 = await client.get("/api/v1/dashboard/snapshot")
    etag = r1.headers["etag"]
    r2 = await client.get("/api/v1/dashboard/snapshot", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    r3 = await client.get("/api/v1/dashboard/snapshot", headers={"If-None-Match": etag})
    assert r3.status_code == 304


@pytest.mark.asyncio
async def test_invalidate_forces_snapshot_rebuild(client):
    r1 = await client.get("/api/v1/dashboard/snapshot")
    etag = r1.headers["etag"]
    await client.get("/api/v1/dashboard/snapshot", headers={"If-None-Match": etag})

    calls = {"n": 0}
    original = build_dashboard_snapshot

    async def counting_build():
        calls["n"] += 1
        return await original()

    with patch(
        "agent.api.dashboard_routes.build_dashboard_snapshot",
        new=AsyncMock(side_effect=counting_build),
    ):
        invalidate_dashboard_snapshot()
        resp = await client.get("/api/v1/dashboard/snapshot", headers={"If-None-Match": etag})
        assert resp.status_code in {200, 304}
        assert calls["n"] == 1
