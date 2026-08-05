"""PostgreSQL MVP 栈集成测试（需本地 M4 Docker 栈）。

启用方式：
  $env:BIANCA_PG_E2E = "1"
  $env:DATABASE_URL = "postgresql+asyncpg://bianca:bianca@127.0.0.1:5432/bianca"
  py -m pytest tests/test_m4_pg_integration.py -m pg -v
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.checkpoint.store import checkpointer_backend, list_checkpoint_threads
from agent.config import clear_settings_cache
from agent.llm.schemas import AnalysisResult, TradeSignal
from agent.main import app
from agent.positions.sync import sync_positions_from_balance
from agent.storage.constants import DEFAULT_AGENT_STRATEGY_ID
from agent.storage.database import close_db, init_db, is_postgres_url, schema_mode
from agent.storage.repository import PositionRepository, StrategyRepository

pytestmark = pytest.mark.pg


def _pg_e2e_enabled() -> bool:
    return os.environ.get("BIANCA_PG_E2E") == "1" and is_postgres_url(
        os.environ.get("DATABASE_URL", "")
    )


@pytest.fixture
async def pg_client():
    if not _pg_e2e_enabled():
        pytest.skip("Set BIANCA_PG_E2E=1 and DATABASE_URL=postgresql+asyncpg://...")

    clear_settings_cache()
    await close_db()
    await init_db()
    assert schema_mode() == "mvp"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()
    clear_settings_cache()


@pytest.mark.asyncio
async def test_pg_schema_mode_and_checkpointer(pg_client):
    resp = await pg_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database_backend"] == "postgresql"
    assert body["schema_mode"] == "mvp"
    assert body["checkpointer_backend"] == "postgresql"
    assert checkpointer_backend() == "postgresql"


@pytest.mark.asyncio
async def test_pg_position_upsert_and_api(pg_client):
    count = await sync_positions_from_balance(
        balance_free={"USDT": 1234.5, "BTC": 0.02},
        symbol="BTCUSDT",
        last_price=65000.0,
    )
    assert count == 2

    repo = PositionRepository()
    rows = await repo.list_by_strategy(DEFAULT_AGENT_STRATEGY_ID)
    symbols = {row.symbol for row in rows}
    assert "USDT" in symbols
    assert "BTC" in symbols

    resp = await pg_client.get("/api/v1/positions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_mode"] == "mvp"
    assert body["total"] >= 2


@pytest.mark.asyncio
async def test_pg_strategy_tick_hold(pg_client):
    create = await pg_client.post(
        "/api/v1/strategies",
        json={"name": "PG趋势", "type": "trend", "execution_mode": "auto"},
    )
    assert create.status_code == 200
    sid = create.json()["id"]

    await pg_client.post(f"/api/v1/strategies/{sid}/start")
    repo = StrategyRepository()
    await repo.update(sid, status="running")

    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 1000.0, "BTC": 0.0}},
    }
    with patch("agent.strategy.engine.fetch_market", AsyncMock(return_value=market)):
        tick = await pg_client.post(f"/api/v1/strategies/{sid}/tick")
    assert tick.status_code == 200
    assert tick.json()["status"] == "hold"


@pytest.mark.asyncio
async def test_pg_checkpoint_after_agent_tick(pg_client):
    thread_id = f"pg-e2e-{uuid.uuid4().hex[:8]}"
    hold = TradeSignal(action="HOLD", symbol="BTCUSDT", confidence=0.5, reason="pg e2e")
    mock_result = AnalysisResult(
        signal=hold,
        raw_output="{}",
        model_used="test:mock",
        prompt_summary="pg",
        auto_execute=False,
        decision_id=f"dec-{uuid.uuid4().hex[:8]}",
        usage=None,
    )
    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 1000.0, "BTC": 0.0}},
    }

    with patch("agent.graph.supervisor.run_analysis_agent", AsyncMock(return_value=mock_result)):
        resp = await pg_client.post(
            "/api/v1/agent/tick",
            json={"thread_id": thread_id, "market_data": {"last": 65000.0}},
        )
    assert resp.status_code == 200

    threads = await list_checkpoint_threads(limit=50)
    thread_ids = {item["thread_id"] for item in threads}
    assert thread_id in thread_ids

    history = await pg_client.get(f"/api/v1/checkpoints/threads/{thread_id}/history")
    assert history.status_code == 200
    assert history.json()["total"] >= 1
