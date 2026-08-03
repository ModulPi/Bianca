from __future__ import annotations

from typing import Any

import ccxt.async_support as ccxt

from agent.config import Settings, get_settings
from agent.exchange._client import format_binance_error
from agent.exchange.futures_demo import build_futures_config


def _coin_symbol(raw: str) -> str:
    """BTCUSDT → BTC/USD:BTC（币本位永续）。"""
    compact = raw.upper().replace("/", "").replace(":", "")
    if compact.endswith("USDT"):
        base = compact[:-4]
        return f"{base}/USD:{base}"
    if "/" in raw:
        return raw
    return f"{compact[:3]}/USD:{compact[:3]}"


class FuturesCoinDemoExchange:
    """币本位合约 Demo/Live 封装。"""

    def __init__(self, settings: Settings | None = None, *, live: bool = False) -> None:
        self._settings = settings or get_settings()
        self._live = live
        self._exchange: ccxt.binancecoinm | None = None

    async def __aenter__(self) -> FuturesCoinDemoExchange:
        config = build_futures_config(self._settings)
        config["options"] = {"defaultType": "delivery"}
        self._exchange = ccxt.binancecoinm(config)
        if not self._live:
            self._exchange.enable_demo_trading(True)
        await self._exchange.load_markets()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    @property
    def exchange(self) -> ccxt.binancecoinm:
        if self._exchange is None:
            raise RuntimeError("FuturesCoinDemoExchange not opened")
        return self._exchange

    async def fetch_balance(self) -> dict[str, Any]:
        return await self.exchange.fetch_balance()

    async def ping(self) -> dict[str, Any]:
        balance = await self.fetch_balance()
        total = balance.get("total") or {}
        btc = float(total.get("BTC") or 0)
        return {"btc_total": btc}

    async def create_market_order(
        self,
        side: str,
        amount: float,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        ccxt_sym = _coin_symbol(symbol or self._settings.trade_symbol)
        if side == "buy":
            return await self.exchange.create_order(
                ccxt_sym,
                "market",
                "buy",
                None,
                None,
                {"quoteOrderQty": amount},
            )
        return await self.exchange.create_order(ccxt_sym, "market", "sell", amount)


async def check_futures_coin(*, settings: Settings | None = None, live: bool = False) -> dict[str, str]:
    cfg = settings or get_settings()
    if not cfg.futures_enabled:
        return {"status": "disabled", "detail": "FUTURES_ENABLED=false"}
    if not cfg.binance_configured:
        return {"status": "not_configured", "detail": "BINANCE_API_KEY/SECRET missing"}
    try:
        async with FuturesCoinDemoExchange(cfg, live=live) as demo:
            result = await demo.ping()
        mode = "live" if live else "demo"
        return {"status": "ok", "detail": f"{mode} coin BTC balance={result['btc_total']:.6f}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": format_binance_error(exc)}
