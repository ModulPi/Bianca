import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Settings, clear_settings_cache
from agent.main import app
from agent.security.crypto import decrypt_secret, encrypt_secret
from agent.storage.database import close_db, init_db
from agent.storage.repository import SessionSummaryRepository
from agent.summary.aggregator import save_interim_snapshot


@pytest.fixture
async def client():
    clear_settings_cache()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()
    clear_settings_cache()


def test_crypto_roundtrip():
    clear_settings_cache()
    cfg = Settings(encryption_key="test-secret-key-for-mvp")
    plain = "sk-test-12345"
    encrypted = encrypt_secret(plain, settings=cfg)
    assert decrypt_secret(encrypted, settings=cfg) == plain


@pytest.mark.asyncio
async def test_api_token_blocks_protected_routes():
    clear_settings_cache()
    with patch.dict("os.environ", {"API_TOKEN": "secret-token"}, clear=False):
        clear_settings_cache()
        await init_db()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/agent/status")
            assert resp.status_code == 401
            resp2 = await ac.get(
                "/api/v1/agent/status",
                headers={"Authorization": "Bearer secret-token"},
            )
            assert resp2.status_code == 200
            health = await ac.get("/api/v1/health")
            assert health.status_code == 200
        await close_db()
        clear_settings_cache()


@pytest.mark.asyncio
@pytest.mark.skip(reason="Web 密钥 CRUD 已从 Agent 产品范围移除")
async def test_secrets_api_requires_encryption_key(client):
    resp = await client.post(
        "/api/v1/secrets/keys",
        json={"name": "binance-main", "key_type": "binance", "value": "abc"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.skip(reason="Web 密钥 CRUD 已从 Agent 产品范围移除")
async def test_secrets_api_create_and_list():
    clear_settings_cache()
    with patch.dict("os.environ", {"ENCRYPTION_KEY": "mvp-test-key"}, clear=False):
        clear_settings_cache()
        await init_db()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create = await ac.post(
                "/api/v1/secrets/keys",
                json={"name": "llm", "key_type": "llm", "value": "sk-abc"},
            )
            assert create.status_code == 200
            body = create.json()
            assert body["masked_value"] == "sk…bc"
            listed = await ac.get("/api/v1/secrets/keys")
            assert listed.status_code == 200
            assert listed.json()["total"] >= 1
        await close_db()
        clear_settings_cache()


@pytest.mark.asyncio
async def test_interim_snapshot_upsert():
    clear_settings_cache()
    await init_db()
    session_id = str(uuid.uuid4())
    started = "2026-08-03T00:00:00+00:00"
    await save_interim_snapshot(
        session_id=session_id,
        started_at=started,
        tick_count=1,
        last_status="ok",
    )
    await save_interim_snapshot(
        session_id=session_id,
        started_at=started,
        tick_count=2,
        last_status="filled",
    )
    row = await SessionSummaryRepository().get_by_id(session_id)
    assert row is not None
    assert row.tick_count == 2
    assert row.ended_at is None
    await close_db()


@pytest.mark.asyncio
async def test_summary_csv_export(client):
    clear_settings_cache()
    await init_db()
    session_id = str(uuid.uuid4())
    await SessionSummaryRepository().save(
        session_id=session_id,
        started_at="2026-08-03T00:00:00+00:00",
        ended_at="2026-08-03T01:00:00+00:00",
        tick_count=3,
        trading_style="conservative",
        usage_json={"total_tokens": 10},
        trades_json={"buy_filled": 1, "sell_filled": 1, "loop_closed": True},
        pnl_json={"realized_usdt": 0.5},
        positions_json={},
        loop_closed=True,
    )
    resp = await client.get(f"/api/v1/summary/sessions/{session_id}/export.csv")
    assert resp.status_code == 200
    assert "session_id" in resp.text
    assert session_id in resp.text
    await close_db()


@pytest.mark.asyncio
async def test_futures_status_fields(client):
    resp = await client.get("/api/v1/futures/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "connectivity" in body
    assert body["enabled"] is False


@pytest.mark.asyncio
async def test_notify_status_includes_email(client):
    resp = await client.get("/api/v1/notify/status")
    assert resp.status_code == 200
    assert "email_configured" in resp.json()


def test_parse_secret_formats():
    from agent.security.secrets_loader import parse_secret_value

    assert parse_secret_value("binance", "k:s") == {
        "binance_api_key": "k",
        "binance_api_secret": "s",
    }
    assert parse_secret_value("llm", "sk-abc") == {"llm_api_key": "sk-abc"}


def test_merge_patch_env_priority():
    from agent.config import Settings
    from agent.security.secrets_loader import _merge_patch

    base = Settings(llm_api_key="", binance_api_key="")
    merged = _merge_patch(base, {"llm_api_key": "sk-db", "binance_api_key": "k", "binance_api_secret": "s"})
    assert merged.llm_api_key == "sk-db"
    assert merged.binance_api_key == "k"

    base_env = Settings(llm_api_key="sk-env")
    merged2 = _merge_patch(base_env, {"llm_api_key": "sk-db"})
    assert merged2.llm_api_key == "sk-env"


@pytest.mark.asyncio
@pytest.mark.skip(reason="Web 密钥 CRUD 已从 Agent 产品范围移除")
async def test_runtime_secrets_reload_after_create():
    clear_settings_cache()
    await init_db()
    with patch.dict("os.environ", {"ENCRYPTION_KEY": "runtime-test-key"}, clear=False):
        clear_settings_cache()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create = await ac.post(
                "/api/v1/secrets/keys",
                json={"name": "llm", "key_type": "llm", "value": "sk-from-db"},
            )
            assert create.status_code == 200
            reload = await ac.post("/api/v1/secrets/reload")
            assert reload.status_code == 200
    await close_db()
    clear_settings_cache()


@pytest.mark.asyncio
async def test_metrics_endpoint(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert b"bianca_agent_ticks_total" in resp.content


def test_resolve_trade_market():
    from agent.config import Settings
    from agent.trading.executor import resolve_trade_market

    cfg = Settings(futures_enabled=True, default_trade_market="spot")
    assert resolve_trade_market({"market": "futures_u"}, cfg) == "futures_u"
    assert resolve_trade_market({"market": "futures_coin"}, cfg) == "futures_coin"
    assert resolve_trade_market({}, cfg) == "spot"
    cfg2 = Settings(futures_enabled=False)
    assert resolve_trade_market({"market": "futures_u"}, cfg2) == "spot"


@pytest.mark.asyncio
@pytest.mark.skip(reason="Web 密钥 CRUD 已从 Agent 产品范围移除")
async def test_secrets_reload_endpoint():
    clear_settings_cache()
    await init_db()
    with patch.dict("os.environ", {"ENCRYPTION_KEY": "reload-key"}, clear=False):
        clear_settings_cache()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/v1/secrets/reload")
            assert resp.status_code == 200
            body = resp.json()
            assert "binance_configured" in body
    await close_db()
    clear_settings_cache()


@pytest.mark.asyncio
async def test_health_includes_ollama_field(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "ollama" in body
    assert "metrics_enabled" in body
