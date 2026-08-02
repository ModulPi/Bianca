from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.storage.json_utils import parse_json_field

from agent.api.schemas import (
    ConfirmPendingResponse,
    MessageResponse,
    PendingSignalItem,
    PendingSignalListResponse,
)
from agent.confirmation.service import confirm_pending_signal, reject_pending_signal
from agent.storage.repository import PendingSignalRepository

router = APIRouter(prefix="/pending-signals")


def _to_item(row) -> PendingSignalItem:
    return PendingSignalItem(
        id=row.id,
        strategy_id=row.strategy_id,
        signal=parse_json_field(row.signal_json),
        status=row.status,
        expires_at=row.expires_at,
        created_at=row.created_at,
        session_id=row.session_id,
        decision_id=row.decision_id,
    )


@router.get("", response_model=PendingSignalListResponse)
async def list_pending_signals(limit: int = 50) -> PendingSignalListResponse:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    repo = PendingSignalRepository()
    rows = await repo.list_pending(limit=limit)
    items = [_to_item(r) for r in rows]
    return PendingSignalListResponse(items=items, total=len(items))


@router.post("/{pending_id}/confirm", response_model=ConfirmPendingResponse)
async def confirm_signal(pending_id: str) -> ConfirmPendingResponse:
    try:
        result = await confirm_pending_signal(pending_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    state = result.get("state") or {}
    return ConfirmPendingResponse(
        status=str(result.get("status", "unknown")),
        message=state.get("message"),
        trade_log_id=state.get("trade_log_id"),
    )


@router.post("/{pending_id}/reject", response_model=MessageResponse)
async def reject_signal(pending_id: str) -> MessageResponse:
    try:
        await reject_pending_signal(pending_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageResponse(message=f"Pending signal {pending_id} rejected")
