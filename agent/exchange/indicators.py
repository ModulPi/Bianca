"""K 线技术指标（纯函数，无网络/IO，可离线单测）。

供 fetch_market_context 使用，把原始 OHLCV 消化成 LLM 可用的趋势/动量/超买超卖信号。
"""
from __future__ import annotations

from typing import Any

# RSI 周期、SMA 快慢周期
RSI_PERIOD = 14
SMA_SHORT = 5
SMA_LONG = 20
# SMA5 相对 SMA20 偏离超过该比例才判 up/down，否则 flat（死区）
TREND_DEADBAND = 0.0005


def sma(closes: list[float], period: int) -> float | None:
    """简单移动平均；数据不足返回 None。"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder 平滑 RSI。数据不足 period+1 根返回 None；零跌幅返回 100；全平返回 None（无法判定）。"""
    if len(closes) < period + 1:
        return None

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    # Wilder 平滑：以首 period 的平均值作为初值，之后递归平滑
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        if avg_gain == 0:
            return None  # 完全横盘，无超买超卖可言
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def summarize_candles(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """把 OHLCV K 线列表消化成指标集。

    输入为 [{t,o,h,l,c,v}...]（按时间升序），返回：
    {sma5, sma20, rsi14, trend, window_change_pct, momentum_5m_pct}
    - 数据不足时对应字段为 None
    - trend 确定性：SMA5 vs SMA20 相对偏离超死区 → up/down，否则 flat
    """
    closes = [float(c["c"]) for c in candles]
    if not closes:
        return {"sma5": None, "sma20": None, "rsi14": None, "trend": None, "window_change_pct": None, "momentum_5m_pct": None}

    sma5 = sma(closes, SMA_SHORT)
    sma20 = sma(closes, SMA_LONG)
    rsi14 = rsi(closes, RSI_PERIOD)

    first_open = float(candles[0]["o"])
    window_change_pct = None
    if first_open:
        window_change_pct = round((closes[-1] - first_open) / first_open * 100.0, 4)

    # 5m 动量：近 12 根内的收盘变化（若无 5m 数据由调用方补充，这里不计算）
    momentum_5m_pct = None

    trend: str | None = None
    if sma5 is not None and sma20 is not None and sma20 != 0:
        deviation = (sma5 - sma20) / sma20
        if deviation > TREND_DEADBAND:
            trend = "up"
        elif deviation < -TREND_DEADBAND:
            trend = "down"
        else:
            trend = "flat"
    elif window_change_pct is not None:
        trend = "up" if window_change_pct > 0 else ("down" if window_change_pct < 0 else "flat")

    return {
        "sma5": round(sma5, 4) if sma5 is not None else None,
        "sma20": round(sma20, 4) if sma20 is not None else None,
        "rsi14": rsi14,
        "trend": trend,
        "window_change_pct": window_change_pct,
        "momentum_5m_pct": momentum_5m_pct,
    }
