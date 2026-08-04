from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.api.schemas import KlineItem, KlineListResponse
from agent.config import get_settings
from agent.market.klines import fetch_klines

router = APIRouter(prefix="/market", tags=["market"])

_ALLOWED_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}


@router.get("/klines", response_model=KlineListResponse)
async def list_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    limit: int = 120,
) -> KlineListResponse:
    if limit < 10 or limit > 500:
        limit = 120
    iv = interval if interval in _ALLOWED_INTERVALS else "1m"
    settings = get_settings()
    if not settings.binance_configured:
        raise HTTPException(status_code=503, detail="Binance API not configured")

    items, source = await fetch_klines(symbol, interval=iv, limit=limit, settings=settings)
    return KlineListResponse(
        items=[KlineItem(**{k: v for k, v in row.items() if k in KlineItem.model_fields}) for row in items],
        total=len(items),
        symbol=symbol.upper(),
        interval=iv,
        source=source,
    )
