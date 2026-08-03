from __future__ import annotations

from fastapi import APIRouter, Response

from agent.config import get_settings
from agent.metrics import metrics_payload

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    cfg = get_settings()
    if not cfg.metrics_enabled:
        return Response(content="# metrics disabled\n", media_type="text/plain")
    body, content_type = metrics_payload()
    return Response(content=body, media_type=content_type)
