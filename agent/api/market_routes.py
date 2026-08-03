from __future__ import annotations

from fastapi import APIRouter

from agent.api.schemas import KlineListResponse
from agent.storage.repository import KlineRepository

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/klines", response_model=KlineListResponse)
async def list_klines(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 100) -> KlineListResponse:
    if limit < 1 or limit > 500:
        limit = 100
    items = await KlineRepository().list_recent(symbol=symbol, interval=interval, limit=limit)
    return KlineListResponse(items=items, total=len(items))
