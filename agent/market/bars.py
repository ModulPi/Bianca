"""K 线解析、时间对齐、缺口检测、回补分片 —— 纯函数，无网络/IO，可离线单测。

时间口径统一用 epoch 毫秒（INTEGER），与 Binance 的 k.t / REST 首列直接对齐。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# 支持周期 → 毫秒
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def interval_ms(interval: str) -> int:
    try:
        return INTERVAL_MS[interval]
    except KeyError:
        raise ValueError(f"unsupported interval: {interval!r}") from None


def last_closed_bar_open(now_ms: int, interval: str) -> int:
    """最近一根**已完成** bar 的开始时间。

    当前正在走的那根 bar 不算 —— 采集层用 k.x 标志过滤，这个函数用于
    缺口检测确定「应该已经收到哪根」。
    """
    step = interval_ms(interval)
    return (now_ms // step) * step - step


def _unwrap_kline(event: dict[str, Any]) -> dict[str, Any] | None:
    """兼容单路与合并（combined）两种 WS 消息形状。

    单路:   {"e": "kline", "s": "BTCUSDT", "k": {...}}
    合并:   {"stream": "btcusdt@kline_1m", "data": {"e": "kline", "k": {...}}}
    """
    data = event.get("data")
    if isinstance(data, dict):
        event = data
    kline = event.get("k")
    return kline if isinstance(kline, dict) else None


def parse_ws_kline(event: dict[str, Any], *, require_closed: bool = True) -> dict[str, Any] | None:
    """把 WS 事件解析成 klines 行。

    默认只在 bar 关闭（k.x == true）时返回 —— 这是协议自带的权威标志，
    实测每根 bar 内会推送多次增量，靠时间推断不可靠（设计文档 §2.3）。
    返回 None 表示「不需要落库」。
    """
    k = _unwrap_kline(event)
    if k is None:
        return None
    if require_closed and not k.get("x"):
        return None

    try:
        row = {
            "time": int(k["t"]),
            "symbol": str(k["s"]),
            "interval": str(k["i"]),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
        }
    except (KeyError, TypeError, ValueError):
        return None

    # 这几个字段缺失不应导致整根 bar 丢弃
    row["quote_volume"] = _opt_float(k.get("q"))
    row["trades"] = _opt_int(k.get("n"))
    row["taker_buy_base"] = _opt_float(k.get("V"))
    row["taker_buy_quote"] = _opt_float(k.get("Q"))
    return row


def parse_rest_row(row: Sequence[Any], symbol: str, interval: str) -> dict[str, Any] | None:
    """解析 Binance REST /api/v3/klines 的一行（原始 12 列）。

    [openTime, o, h, l, c, volume, closeTime, quoteAssetVolume,
     numberOfTrades, takerBuyBaseAssetVolume, takerBuyQuoteAssetVolume, ignore]
    """
    if not row or len(row) < 6:
        return None
    try:
        parsed: dict[str, Any] = {
            "time": int(row[0]),
            "symbol": symbol,
            "interval": interval,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "quote_volume": _opt_float(row[7]) if len(row) > 7 else None,
            "trades": _opt_int(row[8]) if len(row) > 8 else None,
            "taker_buy_base": _opt_float(row[9]) if len(row) > 9 else None,
            "taker_buy_quote": _opt_float(row[10]) if len(row) > 10 else None,
        }
    except (TypeError, ValueError, IndexError):
        return None
    return parsed


def _opt_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def find_gaps(
    stored_times: Sequence[int],
    *,
    start_ms: int,
    end_ms: int,
    interval: str,
) -> list[tuple[int, int]]:
    """找出 [start_ms, end_ms] 内缺失 bar 的连续区间。

    以相邻已存 bar 的间隔判断，复杂度 O(n) —— 不逐根枚举，10 年跨度也能瞬时完成。
    返回 [(gap_start_ms, gap_end_ms)]，两端均为缺失 bar 的开始时间，闭区间。
    """
    step = interval_ms(interval)
    if end_ms < start_ms:
        return []

    ordered = sorted(set(stored_times))
    gaps: list[tuple[int, int]] = []

    if not ordered:
        return [(start_ms, end_ms)]

    first = ordered[0]
    if first > start_ms:
        gaps.append((start_ms, first - step))

    prev = first
    for current in ordered[1:]:
        if current - prev > step:
            gaps.append((prev + step, current - step))
        prev = current

    if end_ms - prev >= step:
        gaps.append((prev + step, end_ms))

    return [(a, b) for a, b in gaps if a <= b]


def count_bars(start_ms: int, end_ms: int, interval: str) -> int:
    """[start_ms, end_ms] 闭区间内按周期对齐的 bar 数量。"""
    step = interval_ms(interval)
    if end_ms < start_ms:
        return 0
    return (end_ms - start_ms) // step + 1


def chunk_ranges(
    start_ms: int,
    end_ms: int,
    interval: str,
    *,
    bars_per_chunk: int = 1000,
) -> list[tuple[int, int]]:
    """把区间切成若干定长分片，供回补并发拉取。每片闭区间，恰好一次 REST 请求。"""
    step = interval_ms(interval)
    if end_ms < start_ms or bars_per_chunk <= 0:
        return []

    ranges: list[tuple[int, int]] = []
    cursor = start_ms
    span = (bars_per_chunk - 1) * step
    while cursor <= end_ms:
        chunk_end = min(cursor + span, end_ms)
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + step
    return ranges
