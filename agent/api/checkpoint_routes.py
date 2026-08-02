from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.api.schemas import CheckpointHistoryResponse, CheckpointThreadListResponse, CheckpointThreadItem
from agent.checkpoint.replay import get_thread_history, list_threads

router = APIRouter(prefix="/checkpoints")


@router.get("/threads", response_model=CheckpointThreadListResponse)
async def checkpoint_threads(limit: int = 50) -> CheckpointThreadListResponse:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    items = await list_threads(limit=limit)
    return CheckpointThreadListResponse(
        items=[CheckpointThreadItem(**item) for item in items],
        total=len(items),
    )


@router.get("/threads/{thread_id}/history", response_model=CheckpointHistoryResponse)
async def checkpoint_thread_history(thread_id: str, limit: int = 20) -> CheckpointHistoryResponse:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    items = await get_thread_history(thread_id, limit=limit)
    if not items:
        threads = await list_threads(limit=200)
        known = {t["thread_id"] for t in threads}
        if thread_id not in known:
            raise HTTPException(status_code=404, detail="Thread not found or no checkpoints")
    return CheckpointHistoryResponse(thread_id=thread_id, items=items, total=len(items))
