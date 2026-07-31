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
    payload = {
        "trading_pair": trade_symbol,
        "max_trade_amount_usdt": max_trade_amount,
        "market_snapshot": market_data,
        "instruction": "Return a single JSON object for the next spot trade decision.",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def summarize_market_for_log(market_data: dict[str, Any]) -> str:
    symbol = market_data.get("symbol", "?")
    last = market_data.get("last")
    return f"{symbol} last={last}"
