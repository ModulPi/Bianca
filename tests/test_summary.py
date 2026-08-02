import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from agent.main import app
from agent.storage.database import close_db, init_db
from agent.storage.repository import DecisionRepository, SessionSummaryRepository, TradeRepository
from agent.summary.pnl import compute_pnl


def test_compute_pnl_round_trip():
    trades = [
        {"side": "BUY", "quantity": 0.001, "price": 60000.0, "status": "filled"},
        {"side": "SELL", "quantity": 0.001, "price": 61000.0, "status": "filled"},
    ]
    pnl = compute_pnl(trades)
    assert pnl.cash_flow_usdt == pytest.approx(1.0, abs=0.01)
    assert pnl.realized_usdt == pytest.approx(1.0, abs=0.01)


@pytest.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()


@pytest.mark.asyncio
async def test_summary_latest_after_persist(client):
    repo = SessionSummaryRepository()
    session_id = str(uuid.uuid4())
    await repo.save(
        session_id=session_id,
        started_at="2026-08-02T07:00:00+00:00",
        ended_at="2026-08-02T07:10:00+00:00",
        tick_count=3,
        trading_style="aggressive",
        usage_json={"llm_calls": 2, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        trades_json={"buy_filled": 1, "sell_filled": 1, "failed": 0, "signal_only": 0, "loop_closed": True},
        pnl_json={"cash_flow_usdt": 0.0, "realized_usdt": 0.0, "unrealized_usdt": 0.0, "total_usdt": 0.0, "daily_pnl_legacy": 0.0},
        positions_json={"base_asset": "BTC", "base_free": 0.0, "usdt_free": 1000.0, "mark_price": 65000.0},
        loop_closed=True,
    )
    resp = await client.get("/api/v1/summary/session/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["trades"]["loop_closed"] is True


@pytest.mark.asyncio
async def test_summary_builds_from_trades_in_window(client):
    trade_repo = TradeRepository()
    decision_repo = DecisionRepository()
    started = "2026-08-02T08:00:00+00:00"
    await trade_repo.save_signal(
        trade_id=f"sum-{uuid.uuid4().hex[:8]}",
        signal={"action": "BUY", "symbol": "BTCUSDT", "confidence": 0.9, "reason": "t"},
        market_data={"last": 65000.0},
        status="filled",
        risk_decision="approved",
        quantity=0.001,
        price=65000.0,
        order_type="MARKET",
    )
    await decision_repo.save(
        decision_id=f"dec-{uuid.uuid4().hex[:8]}",
        model_used="test",
        prompt_summary="s",
        raw_output="{}",
        parsed_signal={"action": "BUY"},
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )
    from agent.summary.aggregator import build_session_summary

    summary = await build_session_summary(
        session_id=str(uuid.uuid4()),
        started_at=started,
        ended_at=None,
        tick_count=1,
        last_status="filled",
    )
    assert summary["trades"]["buy_filled"] >= 1
    assert summary["usage"]["total_tokens"] >= 15
