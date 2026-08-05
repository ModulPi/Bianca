from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, Response

from agent.api.schemas import DashboardPositionsResponse, DashboardSnapshotResponse
from agent.config import get_settings
from agent.dashboard.etag import (
    etag_matches,
    format_etag,
    remember_snapshot,
    snapshot_etag,
    try_fast_not_modified,
)
from agent.dashboard.snapshot import (
    _build_positions,
    _fetch_balance_cached,
    _fetch_tickers_cached,
    build_agent_status,
    build_dashboard_snapshot,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_CACHE_HEADERS = {
    "Cache-Control": "private, max-age=0, must-revalidate",
}


@router.get(
    "/snapshot",
    response_model=DashboardSnapshotResponse,
    responses={304: {"description": "Not Modified"}},
)
async def dashboard_snapshot(
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
):
    fast_etag = try_fast_not_modified(if_none_match)
    if fast_etag is not None:
        return Response(status_code=304, headers={**_CACHE_HEADERS, "ETag": format_etag(fast_etag)})

    snap = await build_dashboard_snapshot()
    etag = snapshot_etag(snap)
    remember_snapshot(snap, etag)

    if etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={**_CACHE_HEADERS, "ETag": format_etag(etag)})

    return JSONResponse(
        content=snap.model_dump(mode="json"),
        headers={**_CACHE_HEADERS, "ETag": format_etag(etag)},
    )


def _parse_symbols(raw: str | None, fallback: list[str]) -> list[str]:
    if not raw:
        return fallback
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    return parts or fallback


@router.get("/positions", response_model=DashboardPositionsResponse)
async def dashboard_positions(symbols: str | None = None) -> DashboardPositionsResponse:
    settings = get_settings()
    agent = await build_agent_status()
    sym_list = _parse_symbols(symbols, agent.symbols or settings.resolved_agent_symbols)
    balance, _ = await _fetch_balance_cached(settings)
    tickers, _ = await _fetch_tickers_cached(settings, sym_list)
    items = _build_positions(balance, tickers, sym_list, agent.trade_market)
    return DashboardPositionsResponse(
        items=items,
        total=len(items),
        generated_at=datetime.now(UTC).isoformat(),
    )
