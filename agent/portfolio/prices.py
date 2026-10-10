"""实盘价格（生产 Binance REST）。

ADR-013 要求行情取实盘、交易走 Demo —— 估值也一样：给持仓定价不能用 demo 的价格，
否则算出来的权益是"演示账户眼里的世界"。

只做一件事：给定资产列表，拿回它们对 USDT 的价格。取不到就是 None，
**不回落、不用最新成交价冒充**（`valuation.py` 会把 None 显式暴露出去）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from agent.config import Settings

logger = logging.getLogger(__name__)

_PRICE_PATH = "/api/v3/ticker/price"


def quote_symbol(asset: str, quote: str = "USDT") -> str:
    return f"{asset.strip().upper()}{quote}"


def parse_price_response(payload: Any) -> dict[str, float]:
    """`/api/v3/ticker/price` 的返回可以是单条或数组，两种都收。"""
    rows = payload if isinstance(payload, list) else [payload]
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        symbol = row.get("symbol")
        price = row.get("price")
        if not symbol or price is None:
            continue
        try:
            out[str(symbol).upper()] = float(price)
        except (TypeError, ValueError):
            continue
    return out


def build_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.market_rest_base_url,
        proxy=settings.market_proxy or None,
        timeout=httpx.Timeout(15.0, connect=10.0),
        headers={"User-Agent": "bianca-portfolio/0.1"},
    )


async def fetch_prices(
    assets: Iterable[str],
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    quote: str = "USDT",
) -> dict[str, float | None]:
    """资产 → 对 `quote` 的价格。取不到的是 None，**不会缺席**。

    没在账户里持有的资产不请求；请求失败时全部返回 None 而不是抛异常 ——
    估值方拿不到价会把它列进 `unpriced`，比整条链断掉更合适。
    """
    wanted = sorted({a.strip().upper() for a in assets if a and a.strip()})
    result: dict[str, float | None] = dict.fromkeys(wanted)
    if not wanted:
        return result

    symbols = [quote_symbol(a, quote) for a in wanted]
    own_client = client is None
    http = client or build_client(settings)
    try:
        resp = await http.get(_PRICE_PATH, params={"symbols": _json_array(symbols)})
        resp.raise_for_status()
        by_symbol = parse_price_response(resp.json())
    except Exception as exc:  # noqa: BLE001 — 取价失败不能让整个快照失败
        logger.warning("实盘取价失败，本轮按取不到处理: %s", exc)
        return result
    finally:
        if own_client:
            await http.aclose()

    for asset in wanted:
        price = by_symbol.get(quote_symbol(asset, quote))
        if price is not None and price > 0:
            result[asset] = price
    return result


def _json_array(symbols: list[str]) -> str:
    """币安对 `symbols` 参数要 JSON 数组字符串，不是重复参数。"""
    import json

    return json.dumps(symbols, separators=(",", ":"))
