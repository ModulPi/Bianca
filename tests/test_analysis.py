import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from agent.graph.analysis_agent import apply_aggressive_nudge, should_auto_execute
from agent.llm.analyzer import parse_trade_signal
from agent.llm.schemas import TradeSignal
from agent.main import app
from agent.storage.database import close_db, init_db


@pytest.fixture
async def client():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_db()


def test_parse_trade_signal_json():
    raw = json.dumps(
        {
            "action": "buy",
            "symbol": "BTCUSDT",
            "amount": 25.0,
            "confidence": 0.72,
            "reason": "短期趋势偏多",
        }
    )
    signal = parse_trade_signal(raw, default_symbol="BTCUSDT")
    assert signal.action == "BUY"
    assert signal.amount == 25.0
    assert signal.confidence == 0.72


def test_parse_trade_signal_strips_markdown_fence():
    raw = """```json
{"action":"HOLD","symbol":"BTCUSDT","amount":null,"confidence":0.5,"reason":"观望"}
```"""
    signal = parse_trade_signal(raw, default_symbol="BTCUSDT")
    assert signal.action == "HOLD"
    assert signal.amount is None


def test_should_auto_execute_respects_flag():
    buy = TradeSignal(action="BUY", symbol="BTCUSDT", amount=10, confidence=0.8, reason="test")
    hold = TradeSignal(action="HOLD", symbol="BTCUSDT", amount=None, confidence=0.5, reason="wait")

    class Cfg:
        llm_auto_execute = True

    class CfgOff:
        llm_auto_execute = False

    assert should_auto_execute(buy, Cfg()) is True
    assert should_auto_execute(hold, Cfg()) is False
    assert should_auto_execute(buy, CfgOff()) is False


def test_aggressive_nudge_buy_when_usdt_available():
    class Cfg:
        trading_style = "aggressive"
        trade_symbol = "BTCUSDT"
        poc_min_trade_usdt = 10.0
        max_trade_amount = 30.0

    hold = TradeSignal(action="HOLD", symbol="BTCUSDT", amount=None, confidence=0.5, reason="wait")
    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 100.0, "BTC": 0.0}},
    }
    out = apply_aggressive_nudge(hold, market, Cfg())
    assert out.action == "BUY"
    assert out.amount is not None
    assert out.amount >= 10.0


def test_aggressive_nudge_sell_when_holding_base():
    class Cfg:
        trading_style = "aggressive"
        trade_symbol = "BTCUSDT"
        poc_min_trade_usdt = 10.0
        max_trade_amount = 30.0

    hold = TradeSignal(action="HOLD", symbol="BTCUSDT", amount=None, confidence=0.5, reason="wait")
    market = {
        "symbol": "BTCUSDT",
        "last": 65000.0,
        "balance": {"free": {"USDT": 5.0, "BTC": 0.001}},
    }
    out = apply_aggressive_nudge(hold, market, Cfg())
    assert out.action == "SELL"
    assert out.amount == 0.001


@pytest.mark.asyncio
async def test_analysis_run_with_mock_llm(client):
    llm_json = json.dumps(
        {
            "action": "HOLD",
            "symbol": "BTCUSDT",
            "amount": None,
            "confidence": 0.6,
            "reason": "Mock: 等待更清晰信号",
        }
    )
    signal = parse_trade_signal(llm_json, default_symbol="BTCUSDT")
    mock_result = AsyncMock(
        return_value=type(
            "R",
            (),
            {
                "signal": signal,
                "raw_output": llm_json,
                "model_used": "deepseek:deepseek-chat",
                "prompt_summary": "BTCUSDT last=65000",
                "auto_execute": False,
                "decision_id": "test-decision-id",
                "analysis_report_id": "test-report-id",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )()
    )

    with patch("agent.api.routes.get_settings") as mock_cfg:
        cfg = mock_cfg.return_value
        cfg.llm_configured = True
        cfg.llm_provider = "deepseek"
        cfg.llm_model = "deepseek-chat"
        cfg.llm_auto_execute = True
        cfg.trade_symbol = "BTCUSDT"

        with patch("agent.graph.analysis_agent.run_analysis_agent", mock_result):
            resp = await client.post(
                "/api/v1/analysis/run",
                json={
                    "market_data": {
                        "symbol": "BTCUSDT",
                        "last": 65000.0,
                        "bid": 64999.0,
                        "ask": 65001.0,
                    }
                },
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["signal"]["action"] == "HOLD"
    assert body["auto_execute"] is False
    assert body["decision_id"] == "test-decision-id"
    assert body["usage"]["total_tokens"] == 150


@pytest.mark.asyncio
async def test_list_decisions(client):
    resp = await client.get("/api/v1/decisions")
    assert resp.status_code == 200
    assert "items" in resp.json()
