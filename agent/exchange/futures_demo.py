from __future__ import annotations

from typing import Any

import ccxt.async_support as ccxt

from agent.config import Settings, get_settings
from agent.exchange._client import format_binance_error


def build_futures_config(settings: Settings) -> dict[str, Any]:
    config: dict[str, Any] = {
        "apiKey": settings.binance_api_key,
        "secret": settings.binance_api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    }
    proxy = settings.binance_proxy.strip()
    if proxy:
        config["aiohttp_proxy"] = proxy
        config["wsProxy"] = proxy
        config["wssProxy"] = proxy
    return config


class FuturesDemoExchange:
    """U 本位合约 Demo/Live 封装。"""

    def __init__(self, settings: Settings | None = None, *, live: bool = False) -> None:
        self._settings = settings or get_settings()
        self._live = live
        self._exchange: ccxt.binanceusdm | None = None

    async def __aenter__(self) -> FuturesDemoExchange:
        self._exchange = ccxt.binanceusdm(build_futures_config(self._settings))
        if not self._live:
            self._exchange.enable_demo_trading(True)
        await self._exchange.load_markets()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    @property
    def exchange(self) -> ccxt.binanceusdm:
        if self._exchange is None:
            raise RuntimeError("FuturesDemoExchange not opened")
        return self._exchange

    async def fetch_balance(self) -> dict[str, Any]:
        return await self.exchange.fetch_balance()

    async def ping(self) -> dict[str, Any]:
        balance = await self.fetch_balance()
        total = balance.get("total") or {}
        usdt = float(total.get("USDT") or 0)
        return {"usdt_total": usdt}

    async def create_market_order(
        self,
        side: str,
        amount: float,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        sym = symbol or self._settings.trade_symbol
        compact = sym.upper().replace("/", "")
        if compact.endswith("USDT"):
            ccxt_sym = f"{compact[:-4]}/USDT:USDT"
        else:
            ccxt_sym = sym
        if side == "buy":
            return await self.exchange.create_order(
                ccxt_sym,
                "market",
                "buy",
                None,
                None,
                {"quoteOrderQty": amount},
            )
        qty = amount
        return await self.exchange.create_order(ccxt_sym, "market", "sell", qty)


async def check_futures_demo(*, settings: Settings | None = None, live: bool = False) -> dict[str, str]:
    cfg = settings or get_settings()
    if not cfg.futures_enabled:
        return {"status": "disabled", "detail": "FUTURES_ENABLED=false"}
    if not cfg.binance_configured:
        return {"status": "not_configured", "detail": "BINANCE_API_KEY/SECRET missing"}
    try:
        async with FuturesDemoExchange(cfg, live=live) as demo:
            result = await demo.ping()
        mode = "live" if live else "demo"
        return {"status": "ok", "detail": f"{mode} USDT balance={result['usdt_total']:.4f}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": format_binance_error(exc)}
