import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.main import app
from agent.runner import AgentRunner, get_runner
from agent.storage.database import close_db, init_db


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


@pytest.mark.asyncio
async def test_agent_status_idle(client):
    resp = await client.get("/api/v1/agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["tick_count"] == 0


@pytest.mark.asyncio
async def test_agent_start_stop(client):
    with patch("agent.runner.run_agent_tick", AsyncMock(return_value={"status": "signal_only"})):
        with patch("agent.api.routes.get_settings") as mock_cfg:
            mock_cfg.return_value.llm_configured = True
            mock_cfg.return_value.agent_tick_interval = 60
            mock_cfg.return_value.llm_auto_execute = True

            start = await client.post("/api/v1/agent/start")
            assert start.status_code == 200
            assert "started" in start.json()["message"].lower()

            status = await client.get("/api/v1/agent/status")
            assert status.json()["running"] is True

            stop = await client.post("/api/v1/agent/stop")
            assert stop.status_code == 200

            status2 = await client.get("/api/v1/agent/status")
            assert status2.json()["running"] is False


@pytest.mark.asyncio
async def test_agent_start_requires_llm(client):
    with patch("agent.api.routes.get_settings") as mock_cfg:
        mock_cfg.return_value.llm_configured = False
        resp = await client.post("/api/v1/agent/start")
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_runner_records_tick(client):
    runner = AgentRunner()
    with patch("agent.runner.run_agent_tick", AsyncMock(return_value={"status": "filled"})):
        with patch("agent.runner.get_settings") as mock_cfg:
            mock_cfg.return_value.agent_tick_interval = 1
            mock_cfg.return_value.llm_configured = True
            await runner.start()
            await asyncio.sleep(0.5)
            await runner.stop()
    snap = await runner.get_snapshot()
    assert snap.tick_count >= 1
    assert snap.last_status == "filled"


@pytest.mark.asyncio
async def test_snapshot_failure_does_not_kill_the_tick(client, caplog):
    """权益快照取不到，整轮 tick 不许崩。

    取不到快照的后果由风控承担：它拿到"日盈亏未知"并拦住新仓 ——
    这是一个**保守**的失败，比 tick 直接崩掉、什么都没记要好。
    """
    from agent.graph.supervisor import _capture_equity_snapshot

    async def boom():
        raise RuntimeError("交易所连不上")

    with patch("agent.portfolio.service.capture", boom):
        with caplog.at_level("WARNING"):
            await _capture_equity_snapshot()  # 不抛

    assert "权益快照采集失败" in caplog.text
