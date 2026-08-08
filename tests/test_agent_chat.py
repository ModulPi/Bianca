import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import clear_settings_cache
from agent.main import app
from agent.storage.database import close_db, init_db


@pytest.fixture
async def client():
    clear_settings_cache()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()
    clear_settings_cache()


@pytest.mark.asyncio
async def test_chat_rule_start(client):
    res = await client.post("/api/v1/agent/chat", json={"message": "启动 Agent"})
    assert res.status_code == 200
    body = res.json()
    assert body["intent"] == "start_agent"
    assert body["reply"]


@pytest.mark.asyncio
async def test_chat_history(client):
    await client.post("/api/v1/agent/chat", json={"message": "查询状态"})
    res = await client.get("/api/v1/agent/chat/history")
    assert res.status_code == 200
    assert len(res.json()["messages"]) >= 2
