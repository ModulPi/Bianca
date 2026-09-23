"""One-off probe for the upcoming market-data module.

Verifies the technical premises before we build the collector:
  1. REST reachability + latency (production vs demo)
  2. How far back 1m history goes          -> confirms the "from 2017" backfill boundary
  3. Raw WS kline payload shape            -> which fields exist (x / n / q / V / Q)
  4. ccxt.pro watch_ohlcv shape            -> what the ccxt wrapper drops
  5. klines rate-limit throughput          -> backfill time estimate
  6. Demo WS endpoint availability         -> production vs demo market-data stream

Usage:
    python scripts/probe_binance_market.py                # all sections
    python scripts/probe_binance_market.py rest history   # selected sections

Fails wholesale when the proxy in .env is not running -- that is expected.
Diagnostic tool; delete once the findings are folded into the design doc.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime

import httpx

sys.path.insert(0, ".")

from agent.config import Settings  # noqa: E402
from agent.exchange._client import build_binance_config  # noqa: E402

SYMBOL = "BTCUSDT"
TIMEFRAME = "1m"
# BTCUSDT spot listed 2017-08-17; probe from a bit earlier to catch the real start.
HISTORY_START_MS = 1_500_000_000_000  # 2017-07-14

OK = "[ OK ]"
FAIL = "[FAIL]"


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# --------------------------------------------------------------------------
# 1 + 2. REST reachability, latency, history depth
# --------------------------------------------------------------------------
def probe_rest(settings: Settings) -> None:
    section("1. REST reachability / latency")
    proxy = settings.binance_proxy.strip() or None
    endpoints = {
        "production": "https://api.binance.com",
        "demo": settings.binance_demo_base_url.rstrip("/"),
    }
    for name, base in endpoints.items():
        url = f"{base}/api/v3/klines"
        params = {"symbol": SYMBOL, "interval": TIMEFRAME, "limit": 2}
        try:
            t0 = time.perf_counter()
            r = httpx.get(url, params=params, proxy=proxy, timeout=20)
            dt = (time.perf_counter() - t0) * 1000
            body = r.json() if r.status_code == 200 else r.text[:100]
            print(f"{OK} {name:11s} HTTP {r.status_code} in {dt:6.0f} ms  {str(body)[:90]}")
        except Exception as exc:  # noqa: BLE001 - probe
            print(f"{FAIL} {name:11s} {type(exc).__name__}: {str(exc)[:100]}")


def probe_history(settings: Settings) -> None:
    section("2. 1m history depth (backfill boundary)")
    proxy = settings.binance_proxy.strip() or None
    url = "https://api.binance.com/api/v3/klines"
    try:
        r = httpx.get(
            url,
            params={
                "symbol": SYMBOL,
                "interval": TIMEFRAME,
                "startTime": HISTORY_START_MS,
                "limit": 1,
            },
            proxy=proxy,
            timeout=20,
        )
        rows = r.json()
        if rows:
            print(f"{OK} earliest {TIMEFRAME} bar available: {_fmt_ms(rows[0][0])}")
            print(f"     row = {rows[0]}")
        else:
            print(f"{FAIL} empty response: {str(rows)[:120]}")
    except Exception as exc:  # noqa: BLE001 - probe
        print(f"{FAIL} {type(exc).__name__}: {str(exc)[:120]}")

    # How many bars between that start and now (backfill size estimate)
    years = (time.time() * 1000 - HISTORY_START_MS) / (365.25 * 24 * 3600 * 1000)
    bars = int(years * 365.25 * 24 * 60)
    print(f"     ~{years:.1f} years -> ~{bars:,} bars -> ~{bars / 1000:,.0f} REST calls @ limit=1000")


# --------------------------------------------------------------------------
# 3. Raw WS kline payload
# --------------------------------------------------------------------------
async def probe_ws_raw(settings: Settings) -> None:
    section("3. Raw WS kline payload (production stream)")
    import websockets

    proxy = settings.binance_proxy.strip() or None
    url = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@kline_{TIMEFRAME}"
    print(f"     connecting {url}")
    print("     waiting for a bar to CLOSE (up to ~90s)...")
    try:
        async with websockets.connect(url, proxy=proxy, open_timeout=20) as ws:
            t0 = time.perf_counter()
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=90)
                msg = json.loads(raw)
                k = msg.get("k", {})
                if k.get("x"):  # x == true -> bar closed, authoritative
                    dt = time.perf_counter() - t0
                    print(f"{OK} closed-bar event after {dt:.1f}s")
                    print(f"     top-level keys : {sorted(msg.keys())}")
                    print(f"     kline keys     : {sorted(k.keys())}")
                    print("     full payload:")
                    for key in sorted(k):
                        print(f"        {key:3s} = {k[key]!r}")
                    print("\n     -> fields ccxt's 6-column OHLCV has no place for:")
                    for key, label in (
                        ("x", "is this bar closed"),
                        ("n", "number of trades"),
                        ("q", "quote-asset volume"),
                        ("V", "taker buy base volume"),
                        ("Q", "taker buy quote volume"),
                    ):
                        print(f"        {key:3s} {label:26s} = {k.get(key, '<MISSING>')!r}")
                    return
                print(f"     ...in-progress bar x=False (n={k.get('n')})", end="\r")
    except Exception as exc:  # noqa: BLE001 - probe
        print(f"\n{FAIL} {type(exc).__name__}: {str(exc)[:140]}")


# --------------------------------------------------------------------------
# 4. ccxt.pro watch_ohlcv shape
# --------------------------------------------------------------------------
async def probe_ccxt_ws(settings: Settings) -> None:
    section("4. ccxt.pro watch_ohlcv shape (what the wrapper drops)")
    import ccxt.pro as ccxtpro

    # NB: build_binance_config sets wsProxy AND wssProxy, which ccxt rejects as
    # conflicting. Use wssProxy only, so this section actually runs.
    config = build_binance_config(settings)
    config.pop("wsProxy", None)
    config["aiohttp_proxy"] = settings.binance_proxy.strip() or None

    ex = ccxtpro.binance(config)
    ex.enable_demo_trading(True)
    try:
        try:
            ex.check_ws_proxy_settings()
            print(f"{OK} proxy settings accepted (wssProxy only)")
        except Exception as exc:  # noqa: BLE001 - probe
            print(f"{FAIL} proxy settings rejected: {exc}")

        print("     resolved REST urls :", json.dumps(ex.urls.get("api", {}), default=str)[:200])
        candles = await asyncio.wait_for(ex.watch_ohlcv(SYMBOL, TIMEFRAME), timeout=60)
        print(f"{OK} watch_ohlcv returned {len(candles)} candles")
        print(f"     first row : {candles[0]}")
        print(f"     last row  : {candles[-1]}  (row width = {len(candles[-1])})")
        print("     -> if width == 6, trades/quote-volume/taker fields are indeed dropped")
    except Exception as exc:  # noqa: BLE001 - probe
        print(f"{FAIL} {type(exc).__name__}: {str(exc)[:140]}")
    finally:
        await ex.close()


# --------------------------------------------------------------------------
# 5. klines rate-limit throughput
# --------------------------------------------------------------------------
def probe_rate(settings: Settings, requests: int = 20) -> None:
    section("5. klines throughput (backfill feasibility)")
    proxy = settings.binance_proxy.strip() or None
    url = "https://api.binance.com/api/v3/klines"
    t0 = time.perf_counter()
    ok = 0
    try:
        with httpx.Client(proxy=proxy, timeout=30) as client:
            for i in range(requests):
                r = client.get(
                    url,
                    params={
                        "symbol": SYMBOL,
                        "interval": TIMEFRAME,
                        "limit": 1000,
                        "startTime": HISTORY_START_MS + i * 1000 * 60_000,
                    },
                )
                if r.status_code == 200:
                    ok += 1
                elif r.status_code in (418, 429):
                    print(f"{FAIL} rate limited at request {i + 1}: HTTP {r.status_code}")
                    break
                else:
                    print(f"{FAIL} unexpected HTTP {r.status_code} at {i + 1}: {r.text[:80]}")
                    break
    except Exception as exc:  # noqa: BLE001 - probe
        print(f"{FAIL} {type(exc).__name__}: {str(exc)[:120]}")
        return

    dt = time.perf_counter() - t0
    if not ok:
        return
    rps = ok / dt
    bars = ok * 1000
    print(f"{OK} {ok}/{requests} requests in {dt:.1f}s -> {rps:.2f} req/s, {bars / dt:,.0f} bars/s")
    years = (time.time() * 1000 - HISTORY_START_MS) / (365.25 * 24 * 3600 * 1000)
    total_bars = int(years * 365.25 * 24 * 60)
    print(f"     full backfill ~{total_bars:,} bars -> ~{total_bars / (bars / dt) / 60:.0f} min")


# --------------------------------------------------------------------------
# 6. Demo WS endpoint
# --------------------------------------------------------------------------
async def _first_kline(ws_url: str, proxy: str | None, timeout: float = 45) -> dict:
    """Connect to a Binance kline stream and return the first kline payload."""
    import websockets

    async with websockets.connect(ws_url, proxy=proxy, open_timeout=20) as ws:
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            k = msg.get("k")
            if k:
                return k


async def _probe_demo_ws_async(settings: Settings) -> None:
    import ccxt.pro as ccxtpro

    config = build_binance_config(settings)
    config.pop("wsProxy", None)
    ex = ccxtpro.binance(config)
    ex.enable_demo_trading(True)
    api = ex.urls.get("api", {})
    for key in sorted(api):
        if "ws" in key.lower():
            print(f"     urls['api']['{key}'] = {api[key]}")
    print("     -> demo HAS its own market-data stream; this is a real choice, not a no-op")

    proxy = settings.binance_proxy.strip() or None
    streams = {
        "production": f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@kline_{TIMEFRAME}",
        "demo": f"wss://demo-stream.binance.com/ws/{SYMBOL.lower()}@kline_{TIMEFRAME}",
    }
    closes: dict[str, float] = {}
    for name, url in streams.items():
        try:
            k = await _first_kline(url, proxy)
            closes[name] = float(k["c"])
            print(
                f"{OK} {name:11s} close={k['c']} trades={k.get('n')} "
                f"quoteVol={k.get('q')} x={k.get('x')}"
            )
        except Exception as exc:  # noqa: BLE001 - probe
            print(f"{FAIL} {name:11s} {type(exc).__name__}: {str(exc)[:110]}")

    if len(closes) == 2:
        prod, demo = closes["production"], closes["demo"]
        diff = abs(prod - demo) / prod * 100
        print(f"\n     production={prod}  demo={demo}  delta={diff:.4f}%")
        if diff < 0.01:
            print("     -> prices agree closely; either stream is usable for the PoC")
        else:
            print("     -> prices DIVERGE; market data must come from production or")
            print("        decisions cannot be validated against real history")


def probe_demo_ws(settings: Settings) -> None:
    section("6. Demo vs production market-data stream")
    asyncio.run(_probe_demo_ws_async(settings))


# --------------------------------------------------------------------------
SECTIONS = {
    "rest": lambda s: probe_rest(s),
    "history": lambda s: probe_history(s),
    "ws": lambda s: asyncio.run(probe_ws_raw(s)),
    "ccxt": lambda s: asyncio.run(probe_ccxt_ws(s)),
    "rate": lambda s: probe_rate(s),
    "demo": lambda s: probe_demo_ws(s),
}


def main() -> None:
    names = sys.argv[1:] or list(SECTIONS)
    unknown = [n for n in names if n not in SECTIONS]
    if unknown:
        print(f"unknown section(s): {unknown}\navailable: {list(SECTIONS)}")
        raise SystemExit(2)

    settings = Settings()
    print(f"symbol={SYMBOL} timeframe={TIMEFRAME}")
    print(f"proxy ={settings.binance_proxy or '<none>'}")
    for name in names:
        SECTIONS[name](settings)
    print("\ndone.")


if __name__ == "__main__":
    main()
