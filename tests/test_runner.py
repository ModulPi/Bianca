from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Settings, clear_settings_cache, set_effective_settings
from agent.main import app
from agent.runner import AgentRunner, WorkerSnapshot, get_runner
from agent.storage.database import close_db, init_db


def _runner_settings(**overrides) -> Settings:
    base = {
        "llm_configured": True,
        "llm_api_key": "sk-test",
        "binance_api_key": "k",
        "binance_api_secret": "s",
        "trade_market": "crypto",
        "agent_symbols": "BTCUSDT",
        "agent_max_parallel": 8,
        "agent_tick_interval": 60,
        "llm_auto_execute": True,
        "execution_mode": "auto",
        "paper_validation_min_hours": 24.0,
        "paper_validation_require_loop": True,
        "agent_stop_on_loop_closed": False,
        "auto_degrade_enabled": True,
        "auto_degrade_failures": 3,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    runner = get_runner()
    if runner.running:
        await runner.stop()
    await close_db()
    clear_settings_cache()


@pytest.mark.asyncio
async def test_agent_status_idle(client):
    resp = await client.get("/api/v1/agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["tick_count"] == 0
    assert body["trade_market"] == "crypto"


@pytest.mark.asyncio
async def test_agent_start_stop(client):
    cfg = _runner_settings()
    clear_settings_cache()
    set_effective_settings(cfg)
    with patch("agent.runner.run_agent_tick", AsyncMock(return_value={"status": "signal_only"})):
        with patch("agent.validation.paper_gate.assert_demo_mode_for_trading", AsyncMock()):
            with patch("agent.validation.paper_gate.ensure_validation_running", AsyncMock()):
                with patch("agent.cache.redis_client.set_active_session", AsyncMock()):
                    start = await client.post("/api/v1/agent/start")
                    assert start.status_code == 200, start.text

                    status = await client.get("/api/v1/agent/status")
                    assert status.json()["running"] is True
                    assert status.json()["symbols"] == ["BTCUSDT"]

                    stop = await client.post("/api/v1/agent/stop")
                    assert stop.status_code == 200

                    status2 = await client.get("/api/v1/agent/status")
                    assert status2.json()["running"] is False
    clear_settings_cache()


@pytest.mark.asyncio
async def test_agent_start_requires_llm(client):
    cfg = _runner_settings(llm_api_key="", llm_provider="deepseek")
    with patch("agent.api.routes.get_settings", return_value=cfg):
        resp = await client.post("/api/v1/agent/start")
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_runner_records_tick(client):
    cfg = _runner_settings()
    runner = AgentRunner()
    runner._snapshot.running = True
    runner._snapshot.symbols = ["BTCUSDT"]
    runner._snapshot.workers = {"BTCUSDT": WorkerSnapshot(symbol="BTCUSDT")}
    with patch("agent.runner.run_agent_tick", AsyncMock(return_value={"status": "filled"})):
        with patch("agent.runner.get_settings", return_value=cfg):
            with patch("agent.degradation.get_settings", return_value=cfg):
                await runner._run_one_tick(cfg, "BTCUSDT")
    snap = await runner.get_snapshot()
    assert snap.tick_count >= 1
    assert snap.last_status == "filled"
