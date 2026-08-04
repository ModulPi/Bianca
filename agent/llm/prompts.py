from __future__ import annotations

import json
from typing import Any

from agent.config import Settings

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

AGGRESSIVE_SYSTEM_PROMPT = """You are Bianca, an aggressive crypto spot trading agent on Binance DEMO (simulated funds).
Your PoC mission: complete at least one BUY then one SELL round-trip quickly. Profit/loss does NOT matter.

Rules:
- action must be exactly one of: BUY, SELL, HOLD
- symbol must match the provided trading pair
- amount: BUY = USDT notional; SELL = base asset quantity (sell available free balance); HOLD = null
- confidence: float between 0.0 and 1.0
- reason: concise Chinese explanation
- STRONGLY prefer BUY or SELL over HOLD — HOLD only if balance is truly insufficient for any trade
- If free USDT >= min_trade_usdt and little/no base asset → BUY (use 50–90% of max_trade_amount or available USDT)
- If free base asset value >= min_trade_usdt → SELL (sell most/all free base quantity to close the loop)
- Minute-level cadence: act decisively each tick; do not wait for perfect setups
- Never exceed max_trade_amount (USDT notional) on BUY
- Output valid JSON only, no markdown fences or extra text

JSON schema:
{
  "action": "BUY|SELL|HOLD",
  "symbol": "BTCUSDT",
  "amount": number|null,
  "confidence": 0.0-1.0,
  "reason": "string"
}"""


def base_asset_for_symbol(symbol: str) -> str:
    sym = symbol.upper().replace("/", "")
    if sym.endswith("USDT"):
        return sym[:-4]
    if "/" in symbol.upper():
        return symbol.upper().split("/")[0]
    return sym


def normalize_symbol(symbol: str) -> str:
    """BTC/USDT → BTCUSDT for ccxt unified symbols."""
    return symbol.upper().replace("/", "")


def resolve_worker_symbol(
    *,
    market_data: dict[str, Any] | None = None,
    signal: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    """当前 Worker tick 对应的交易对（优先 market_data，其次 signal，最后 settings）。"""
    cfg = settings or get_settings()
    if market_data and market_data.get("symbol"):
        return normalize_symbol(str(market_data["symbol"]))
    if signal and signal.get("symbol"):
        return normalize_symbol(str(signal["symbol"]))
    return normalize_symbol(cfg.trade_symbol)


def get_system_prompt(settings: Settings) -> str:
    if settings.trading_style == "aggressive":
        return AGGRESSIVE_SYSTEM_PROMPT
    return SYSTEM_PROMPT


def build_user_prompt(
    market_data: dict[str, Any],
    *,
    max_trade_amount: float,
    trade_symbol: str,
    trading_style: str = "conservative",
    min_trade_usdt: float = 10.0,
) -> str:
    balance = market_data.get("balance") or {}
    free = balance.get("free") or {}
    base = base_asset_for_symbol(trade_symbol)
    payload = {
        "trading_pair": trade_symbol,
        "trading_style": trading_style,
        "max_trade_amount_usdt": max_trade_amount,
        "min_trade_usdt": min_trade_usdt,
        "market_snapshot": {
            k: v for k, v in market_data.items() if k != "balance"
        },
        "account_balance": {
            "free_usdt": free.get("USDT"),
            "free_base": free.get(base),
            "base_asset": base,
        },
        "instruction": (
            "Return a single JSON object for the next spot trade decision. "
            "PoC aggressive goal: complete BUY→SELL loop on demo account."
            if trading_style == "aggressive"
            else "Return a single JSON object for the next spot trade decision."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def summarize_market_for_log(market_data: dict[str, Any]) -> str:
    symbol = market_data.get("symbol", "?")
    last = market_data.get("last")
    balance = market_data.get("balance") or {}
    free = balance.get("free") or {}
    usdt = free.get("USDT")
    base = base_asset_for_symbol(symbol if symbol != "?" else "BTCUSDT")
    base_qty = free.get(base)
    parts = [f"{symbol} last={last}"]
    if usdt is not None or base_qty is not None:
        parts.append(f"free USDT={usdt} {base}={base_qty}")
    return " ".join(parts)
