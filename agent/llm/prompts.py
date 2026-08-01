from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """You are Bianca, a crypto spot trading analysis agent.
Analyze the given market snapshot and output ONE trading decision as JSON only.

Rules:
- action must be exactly one of: BUY, SELL, HOLD
- symbol must match the provided trading pair
- amount: for BUY use USDT notional; for SELL use base asset quantity; for HOLD use null
- confidence: float between 0.0 and 1.0
- reason: concise explanation in the same language as the user context (default Chinese)
- Prefer HOLD when data is insufficient or risk is unclear
- Never suggest amount above max_trade_amount (USDT) for BUY
- Output valid JSON only, no markdown fences or extra text

JSON schema:
{
  "action": "BUY|SELL|HOLD",
  "symbol": "BTCUSDT",
  "amount": number|null,
  "confidence": 0.0-1.0,
  "reason": "string"
}"""


def build_user_prompt(
    market_data: dict[str, Any],
    *,
    max_trade_amount: float,
    trade_symbol: str,
) -> str:
    """构造 LLM 用户提示词。

    只提取精简字段进 market_snapshot（不整包 dump market_data，避免 K 线撑爆 token）；
    有 indicators/candles 时追加 technical_context（含 SMA/RSI/趋势/收盘价序列）。
    """
    symbol = market_data.get("symbol") or trade_symbol

    snapshot = {
        "symbol": symbol,
        "last": market_data.get("last"),
        "bid": market_data.get("bid"),
        "ask": market_data.get("ask"),
        "high_24h": market_data.get("high_24h"),
        "low_24h": market_data.get("low_24h"),
        "change_24h_pct": market_data.get("change_24h_pct"),
        "volume_24h_quote_usdt": market_data.get("volume_24h_quote_usdt"),
    }

    payload: dict[str, Any] = {
        "trading_pair": symbol,
        "max_trade_amount_usdt": max_trade_amount,
        "market_snapshot": {k: v for k, v in snapshot.items() if v is not None},
        "instruction": (
            "Analyze using trend, momentum, RSI, and volume. "
            "Reason briefly; do not overthink. "
            "Return a single JSON object for the next spot trade decision. "
            "Act on clear signals; HOLD only when the setup is genuinely unclear."
        ),
    }

    indicators = market_data.get("indicators") or {}
    closes = [float(c["c"]) for c in (market_data.get("candles") or []) if isinstance(c, dict)]
    if indicators or closes:
        technical: dict[str, Any] = {"timeframe": "1h"}
        if closes:
            technical["closes"] = [round(c, 4) for c in closes]
        for key in ("sma5", "sma20", "rsi14", "trend", "window_change_pct", "momentum_5m_pct"):
            value = indicators.get(key)
            if value is not None:
                technical[key] = value
        payload["technical_context"] = technical

    return json.dumps(payload, ensure_ascii=False, indent=2)


def summarize_market_for_log(market_data: dict[str, Any]) -> str:
    symbol = market_data.get("symbol", "?")
    last = market_data.get("last")
    return f"{symbol} last={last}"
