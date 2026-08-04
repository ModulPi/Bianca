from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, Response

from agent.api.schemas import DashboardSnapshotResponse
from agent.dashboard.etag import (
    etag_matches,
    format_etag,
    remember_snapshot,
    snapshot_etag,
    try_fast_not_modified,
)
from agent.dashboard.snapshot import build_dashboard_snapshot

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
