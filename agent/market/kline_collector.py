from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from agent.config import Settings, get_settings
from agent.exchange.spot_demo import SpotDemoExchange
from agent.llm.prompts import normalize_symbol
from agent.storage.database import is_postgres_url
from agent.storage.repository import KlineRepository

logger = logging.getLogger(__name__)

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(frozen=True, slots=True)
class KlineBar:
    time: datetime
    symbol: str
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int | None = None


def collector_sleep_seconds(interval: str) -> int:
    return max(30, TIMEFRAME_SECONDS.get(interval, 60))


def resolve_kline_symbols(settings: Settings) -> list[str]:
    raw = settings.kline_symbols.strip()
    if not raw:
        return [normalize_symbol(settings.trade_symbol)]
    return [normalize_symbol(part.strip()) for part in raw.split(",") if part.strip()]


def ohlcv_to_bars(
    ohlcv: list[list],
    *,
    symbol: str,
    interval: str,
    skip_last: bool = True,
) -> list[KlineBar]:
    rows = ohlcv[:-1] if skip_last and len(ohlcv) > 1 else ohlcv
    bars: list[KlineBar] = []
    for candle in rows:
        if len(candle) < 6:
            continue
        ts_ms, open_, high, low, close, volume = candle[:6]
        bars.append(
            KlineBar(
                time=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                symbol=symbol,
                interval=interval,
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume or 0),
            )
        )
    return bars


def should_run_collector(settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    return (
        is_postgres_url(cfg.database_url)
        and cfg.kline_collector_enabled
        and cfg.binance_configured
    )


async def collect_klines_once(*, settings: Settings | None = None) -> int:
    """拉取并写入 K 线，返回新插入条数。非 MVP 或未配置时返回 0。"""
    cfg = settings or get_settings()
    if not should_run_collector(cfg):
        return 0

    repo = KlineRepository()
    interval = cfg.kline_interval
    inserted = 0

    async with SpotDemoExchange(cfg) as demo:
        for symbol in resolve_kline_symbols(cfg):
            since_ms: int | None = None
            latest = await repo.get_latest_time(symbol, interval)
            if latest is not None:
                since_ms = int(latest.timestamp() * 1000) + 1

            ohlcv = await demo.fetch_ohlcv(
                symbol,
                timeframe=interval,
                since=since_ms,
                limit=cfg.kline_fetch_limit,
            )
            bars = ohlcv_to_bars(ohlcv, symbol=symbol, interval=interval)
            if not bars:
                continue
            count = await repo.insert_bars(bars)
            inserted += count
            logger.info(
                "klines collected symbol=%s interval=%s fetched=%d inserted=%d",
                symbol,
                interval,
                len(bars),
                count,
            )

    return inserted


async def run_kline_collector_loop() -> None:
    cfg = get_settings()
    if not should_run_collector(cfg):
        logger.info("K 线采集未启用（需 PostgreSQL MVP + BINANCE 配置 + KLINE_COLLECTOR_ENABLED）")
        return

    sleep_s = collector_sleep_seconds(cfg.kline_interval)
    logger.info(
        "K 线采集已启动 interval=%s symbols=%s every=%ds",
        cfg.kline_interval,
        ",".join(resolve_kline_symbols(cfg)),
        sleep_s,
    )
    while True:
        try:
            await collect_klines_once()
        except Exception:  # noqa: BLE001
            logger.exception("klines collect failed")
        await asyncio.sleep(sleep_s)
