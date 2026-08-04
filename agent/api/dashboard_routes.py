from __future__ import annotations

from fastapi import APIRouter

from agent.api.schemas import DashboardSnapshotResponse
from agent.dashboard.snapshot import build_dashboard_snapshot

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/snapshot", response_model=DashboardSnapshotResponse)
async def dashboard_snapshot() -> DashboardSnapshotResponse:
    return await build_dashboard_snapshot()
