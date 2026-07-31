from __future__ import annotations

from typing import Any

from agent.config import Settings


def build_binance_config(settings: Settings) -> dict[str, Any]:
    """Shared ccxt config for Binance Demo spot (REST + WebSocket)."""
    config: dict[str, Any] = {
        "apiKey": settings.binance_api_key,
        "secret": settings.binance_api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    proxy = settings.binance_proxy.strip()
    if proxy:
        config["aiohttp_proxy"] = proxy
        config["wsProxy"] = proxy
        config["wssProxy"] = proxy
    return config


def format_binance_error(exc: Exception) -> str:
    message = str(exc)
    if "451" in message or "restricted location" in message.lower():
        return (
            "Binance API 451: 当前网络所在地区被限制访问。"
            "请在 .env 设置 BINANCE_PROXY（Docker 示例：http://host.docker.internal:7890），"
            "或为本机/容器配置可访问币安的代理/VPN 后重启 API。"
            f" 原始错误: {message}"
        )
    return message
