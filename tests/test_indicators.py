import json

from agent.exchange.indicators import rsi, sma, summarize_candles
from agent.llm.prompts import build_user_prompt


def _candles_from_closes(closes, start_open=None):
    """把收盘价序列合成 [{t,o,h,l,c,v}] K 线，open 默认取前一根 close。"""
    candles = []
    prev = start_open if start_open is not None else closes[0]
    for i, c in enumerate(closes):
        candles.append({"t": i, "o": prev, "h": max(prev, c), "l": min(prev, c), "c": c, "v": 1.0})
        prev = c
    return candles


# ---------- SMA ----------

def test_sma_basic():
    assert sma([1, 2, 3, 4, 5], 5) == 3.0


def test_sma_insufficient_data_returns_none():
    assert sma([1, 2, 3], 5) is None


# ---------- RSI ----------

def test_rsi_constant_rise_is_100():
    assert rsi(list(range(1, 31))) == 100.0


def test_rsi_constant_fall_is_0():
    assert rsi(list(range(30, 0, -1))) == 0.0


def test_rsi_flat_is_none():
    assert rsi([5.0] * 30) is None


def test_rsi_insufficient_data_is_none():
    assert rsi([1.0] * 14) is None


def test_rsi_upward_bias_between_50_and_100():
    # 涨多跌少的序列，RSI 应落在 (50, 100]
    closes = [10, 12, 11, 13, 12, 14, 13, 15, 14, 16, 15, 17, 16, 18, 17, 19]
    value = rsi(closes)
    assert value is not None
    assert 50 < value <= 100


# ---------- summarize_candles ----------

def test_summarize_candles_uptrend():
    closes = list(range(100, 124))
    info = summarize_candles(_candles_from_closes(closes))
    assert info["trend"] == "up"
    assert info["sma5"] == sum(closes[-5:]) / 5
    assert info["window_change_pct"] > 0


def test_summarize_candles_downtrend():
    closes = list(range(123, 99, -1))
    info = summarize_candles(_candles_from_closes(closes))
    assert info["trend"] == "down"
    assert info["window_change_pct"] < 0


def test_summarize_candles_flat():
    info = summarize_candles(_candles_from_closes([100.0] * 24))
    assert info["trend"] == "flat"
    assert info["sma5"] == 100.0
    assert info["sma20"] == 100.0


def test_summarize_candles_too_few_for_rsi():
    info = summarize_candles(_candles_from_closes(list(range(5))))
    assert info["rsi14"] is None
    assert info["sma20"] is None


# ---------- build_user_prompt ----------

def test_build_user_prompt_with_rich_data_has_technical_context():
    candles = _candles_from_closes(list(range(100, 124)))
    market_data = {
        "symbol": "BTC/USDT",
        "last": 123.0,
        "bid": 122.9,
        "ask": 123.1,
        "timestamp": 1234567890,
        "high_24h": 130.0,
        "low_24h": 95.0,
        "change_24h_pct": 2.5,
        "volume_24h_quote_usdt": 5_000_000.0,
        "candles": candles,
        "indicators": summarize_candles(candles),
    }
    prompt = build_user_prompt(market_data, max_trade_amount=50, trade_symbol="BTCUSDT")
    payload = json.loads(prompt)
    assert "technical_context" in payload
    tc = payload["technical_context"]
    assert "closes" in tc and len(tc["closes"]) == 24
    assert tc["trend"] == "up"
    assert tc["sma5"] is not None
    # 精简 prompt：不得把完整 K 线对象（含 o/h/l/v/t 键）塞进去
    assert '"candles"' not in prompt
    assert '"indicators"' not in prompt
    # 24h 字段在 market_snapshot 里
    assert payload["market_snapshot"]["change_24h_pct"] == 2.5


def test_build_user_prompt_minimal_data_degrades_gracefully():
    market_data = {"symbol": "BTCUSDT", "last": 63000.0, "bid": 62999.0, "ask": 63001.0}
    prompt = build_user_prompt(market_data, max_trade_amount=50, trade_symbol="BTCUSDT")
    payload = json.loads(prompt)
    assert "technical_context" not in payload
    assert payload["market_snapshot"]["last"] == 63000.0
