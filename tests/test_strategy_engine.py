from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.skip(reason="策略模板 API 已从 Agent 产品范围移除")

from backend.config import Settings, clear_settings_cache
from backend.main import app
from backend.infrastructure.storage.database import close_db, init_db
from backend.infrastructure.storage.repository import StrategyRepository
from backend.domain.strategy.base import StrategySignal, with_market
from backend.domain.strategy.engine import execute_signal_pipeline, run_strategy_tick
from backend.domain.trading.executor import resolve_trade_market


@pytest.fixture
async def client():
    clear_settings_cache()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()
    clear_settings_cache()


def test_strategy_signal_includes_market():
    sig = with_market(
        StrategySignal(action="BUY", symbol="BTCUSDT", amount=10.0, confidence=0.9, reason="test"),
        "futures_u",
    )
    body = sig.to_dict()
    assert body["market"] == "futures_u"


def test_resolve_trade_market_from_strategy_row():
    cfg = Settings(futures_enabled=True)
    assert resolve_trade_market({"market": "futures_coin"}, cfg) == "futures_coin"


@pytest.mark.asyncio
async def test_strategy_tick_skips_futures_when_disabled(client):
    create = await client.post(
        "/api/v1/strategies",
        json={
            "name": "FuturesGrid",
            "type": "grid",
            "execution_mode": "auto",
            "market": "futures_u",
        },
    )
    sid = create.json()["id"]
    repo = StrategyRepository()
    await repo.update(sid, status="running")

    tick = await run_strategy_tick(sid)
    assert tick["status"] == "skipped"
    assert "FUTURES_ENABLED" in (tick.get("reason") or "")


@pytest.mark.asyncio
async def test_execute_pipeline_calls_paper_gate():
    clear_settings_cache()
    await init_db()
    signal = with_market(
        StrategySignal(action="BUY", symbol="BTCUSDT", amount=10.0, confidence=0.9, reason="test"),
        "spot",
    )
    market = {"symbol": "BTCUSDT", "last": 65000.0, "balance": {"free": {"USDT": 100.0}}}

    with patch("backend.domain.strategy.engine.assert_demo_mode_for_trading", AsyncMock()) as gate:
        with patch("backend.domain.strategy.engine.run_risk_agent", AsyncMock(return_value={"risk_decision": {"approved": False}})):
            result = await execute_signal_pipeline(
                signal,
                market,
                execution_mode="auto",
                settings=Settings(),
            )
    gate.assert_awaited_once()
    assert result["status"] == "risk_rejected"
    await close_db()


@pytest.mark.asyncio
async def test_strategy_tick_passes_market_to_signal(client):
    create = await client.post(
        "/api/v1/strategies",
        json={"name": "SpotDCA", "type": "dca", "execution_mode": "auto", "market": "spot"},
    )
    sid = create.json()["id"]
    repo = StrategyRepository()
    await repo.update(sid, status="running")

    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "market": "spot",
        "balance": {"free": {"USDT": 1000.0, "BTC": 0.0}},
    }
    captured: dict = {}

    async def _capture_pipeline(signal, market_data, **kwargs):
        captured["market"] = signal.to_dict().get("market")
        return {"status": "hold"}

    with patch("backend.domain.strategy.engine.fetch_market_snapshot", AsyncMock(return_value=market)):
        with patch("backend.domain.strategy.engine.execute_signal_pipeline", side_effect=_capture_pipeline):
            with patch("backend.domain.strategy.engine.evaluate_strategy") as mock_eval:
                from backend.domain.strategy.base import StrategyEvalResult, StrategySignal

                mock_eval.return_value = StrategyEvalResult(
                    signal=StrategySignal(
                        action="BUY",
                        symbol="BTCUSDT",
                        amount=10.0,
                        confidence=0.9,
                        reason="dca",
                    )
                )
                await run_strategy_tick(sid)
    assert captured.get("market") == "spot"
    await close_db()
