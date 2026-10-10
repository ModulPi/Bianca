"""近期高点 —— 止损的基准（ADR-026）。

**为什么不读行情库。** 实测 `data/market.db` 只有 BTCUSDT/1m 一条序列，最后一根 bar
停在 2026-09-24T14:32 UTC；而当时（2026-10-10T17:14 UTC）已经过去 16 天 ——
采集器自 2026-10-09 停摆。拿 16 天前的高点当"近期高点"，算出来的回撤与当下无关。
所以这里走**生产 REST 的 `/api/v3/klines`**，和 `prices.py` 用同一套 base_url 与代理，
同时满足 ADR-013（行情取实盘）。行情库恢复供数后可以再换回来，那是另一个决定。

**只用已完成的 bar**，与行情模块 `KlineRepository.recent_closes` 的约定一致：
未收盘那根的高点还在动，把它当历史高点会让止损值在同一根 bar 里反复变。

**这里只出"高点"这一半。** 另一半是现价，取自最新权益快照的 `position.price`
（快照采集时由 `valuation` 经 `prices.fetch_prices` 取到），**不在裁决时重新取价**。
所以判出来的回撤用的是快照那一刻的价，可能已经过去几分钟 —— 它宁可滞后也不自相矛盾：
事后能拿同一张快照把当时的裁决复算出来。

取不到就是 None —— 不回落、不用别的价冒充。风控拿到 None 会拦住开仓，
因为"保护不成立"和"没有风险"是两件事。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from agent.config import Settings
from agent.portfolio.prices import build_client, quote_symbol

logger = logging.getLogger(__name__)

_KLINES_PATH = "/api/v3/klines"

# 一根 bar 的毫秒宽度。币安 interval 字符串 → 毫秒。
_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def interval_ms(interval: str) -> int | None:
    """`"1m"` → 60000。认不出就返回 None —— 不猜。"""
    text = interval.strip().lower()
    digits = text[:-1]
    unit = text[-1:]
    if not digits.isdigit() or unit not in _UNIT_MS:
        return None
    return int(digits) * _UNIT_MS[unit]


def bars_needed(lookback_hours: int, interval: str) -> int | None:
    """覆盖 `lookback_hours` 需要几根 bar。多要一根，免得边界上少一截。"""
    width = interval_ms(interval)
    if width is None or width <= 0:
        return None
    return int(lookback_hours * 3_600_000 // width) + 1


def pick_interval(lookback_hours: int, *, max_bars: int = 1000) -> str | None:
    """挑一个粒度，使回看窗口的 bar 数不超过 `max_bars`（币安单次上限）。

    取不到合适粒度就返回 None —— 由调用方当成"拿不到高点"处理。
    """
    for interval in ("1m", "5m", "15m", "1h", "4h", "1d"):
        need = bars_needed(lookback_hours, interval)
        if need is not None and need <= max_bars:
            return interval
    return None


def parse_klines(payload: Any, *, now_ms: int) -> list[dict[str, float]]:
    """币安 kline 数组 → 字典列表。**丢掉未收盘的那根。**

    币安的行是定长数组：`[open_time, open, high, low, close, volume, close_time, ...]`。
    收盘判据用 `close_time < now`：最后一根的 `close_time` 在未来，说明它还在长。
    """
    rows = payload if isinstance(payload, list) else []
    out: list[dict[str, float]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 7:
            continue
        try:
            open_time = float(row[0])
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            close_time = float(row[6])
        except (TypeError, ValueError):
            continue
        if close_time >= now_ms:
            continue  # 未收盘，跳过
        out.append({"open_time": open_time, "high": high, "low": low, "close": close})
    return out


def high_of(bars: Iterable[Mapping[str, float]]) -> float | None:
    """已完成 bar 里的最高价。一根都没有就是 None。"""
    values = [b["high"] for b in bars if b.get("high") is not None]
    return max(values) if values else None


def parse_high_response(payload: Any, *, now_ms: int) -> float | None:
    return high_of(parse_klines(payload, now_ms=now_ms))


async def fetch_recent_highs(
    assets: Iterable[str],
    *,
    settings: Settings,
    lookback_hours: int | None = None,
    interval: str | None = None,
    quote: str = "USDT",
    client: httpx.AsyncClient | None = None,
    now_ms: int | None = None,
) -> dict[str, float | None]:
    """资产 → 该资产对 `quote` 的近期最高价。取不到的是 None，**不会缺席**。

    与 `fetch_prices` 同样的失败策略：单个 symbol 失败不连累其它，整条链不断。
    粒度缺省按回看窗口自动挑（见 `pick_interval`）。
    """
    import time

    wanted = sorted({a.strip().upper() for a in assets if a and a.strip()})
    result: dict[str, float | None] = dict.fromkeys(wanted)
    if not wanted:
        return result

    hours = lookback_hours if lookback_hours is not None else settings.stop_lookback_hours
    chosen = interval or pick_interval(hours)
    if chosen is None:
        logger.warning("挑不出能覆盖 %s 小时的粒度，本轮按拿不到高点处理", hours)
        return result

    limit = bars_needed(hours, chosen)
    if limit is None:
        return result
    moment = now_ms if now_ms is not None else int(time.time() * 1000)

    own_client = client is None
    http = client or build_client(settings)
    try:
        for asset in wanted:
            symbol = quote_symbol(asset, quote)
            try:
                resp = await http.get(
                    _KLINES_PATH,
                    params={"symbol": symbol, "interval": chosen, "limit": min(limit, 1000)},
                )
                resp.raise_for_status()
                result[asset] = parse_high_response(resp.json(), now_ms=moment)
            except Exception as exc:  # noqa: BLE001 — 单个资产失败不连累其它
                logger.warning("取 %s 的近期高点失败，按拿不到处理: %s", symbol, exc)
    finally:
        if own_client:
            await http.aclose()

    return result
