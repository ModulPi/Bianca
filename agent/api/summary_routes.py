from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from agent.api.schemas import SessionListResponse, SessionSummaryResponse
from agent.config import get_settings
from agent.runner import get_runner
from agent.storage.repository import SessionSummaryRepository
from agent.summary.aggregator import build_session_summary, close_session
from agent.summary.serialize import session_row_to_summary

router = APIRouter(prefix="/summary")


def _to_response(data: dict) -> SessionSummaryResponse:
    return SessionSummaryResponse(**data)


@router.get("/session/current", response_model=SessionSummaryResponse)
async def summary_session_current() -> SessionSummaryResponse:
    runner = get_runner()
    snap = await runner.get_snapshot()
    if not snap.running or not snap.session_id or not snap.session_started_at:
        raise HTTPException(status_code=404, detail="No active agent session")
    data = await build_session_summary(
        session_id=snap.session_id,
        started_at=snap.session_started_at,
        ended_at=None,
        tick_count=snap.tick_count,
        last_status=snap.last_status,
        settings=get_settings(),
    )
    return _to_response(data)


@router.get("/session/latest", response_model=SessionSummaryResponse)
async def summary_session_latest() -> SessionSummaryResponse:
    repo = SessionSummaryRepository()
    row = await repo.get_latest()
    if row is None:
        raise HTTPException(status_code=404, detail="No closed session found")
    return _to_response(session_row_to_summary(row))


@router.get("/sessions", response_model=SessionListResponse)
async def summary_sessions(limit: int = 20, offset: int = 0) -> SessionListResponse:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    repo = SessionSummaryRepository()
    rows = await repo.list_recent(limit=limit, offset=offset)
    items = [_to_response(session_row_to_summary(r)) for r in rows]
    return SessionListResponse(items=items, total=len(items))


@router.get("/sessions/{session_id}", response_model=SessionSummaryResponse)
async def summary_session_by_id(session_id: str) -> SessionSummaryResponse:
    repo = SessionSummaryRepository()
    row = await repo.get_by_id(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _to_response(session_row_to_summary(row))


@router.get("/daily", response_model=SessionListResponse)
async def summary_daily(date: str | None = None) -> SessionListResponse:
    day = date or datetime.now(UTC).date().isoformat()
    repo = SessionSummaryRepository()
    rows = await repo.list_recent(limit=100)
    items = [
        _to_response(session_row_to_summary(r))
        for r in rows
        if r.started_at.startswith(day)
    ]
    return SessionListResponse(items=items, total=len(items))


@router.post("/sessions/{session_id}/close", response_model=SessionSummaryResponse)
async def summary_close_session(session_id: str) -> SessionSummaryResponse:
    runner = get_runner()
    snap = await runner.get_snapshot()
    if snap.session_id != session_id:
        raise HTTPException(status_code=404, detail="Session not active or ID mismatch")
    if not snap.session_started_at:
        raise HTTPException(status_code=400, detail="Session start time missing")
    data = await close_session(
        session_id=session_id,
        started_at=snap.session_started_at,
        tick_count=snap.tick_count,
        last_status=snap.last_status,
        settings=get_settings(),
    )
    return _to_response(data)
