from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.websockets import WebSocket

from agent.config import get_settings

_PUBLIC_PREFIXES = (
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/v1/health",
    "/metrics",
)


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PREFIXES:
        return True
    if path.startswith("/docs") or path.startswith("/redoc"):
        return True
    return False


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token")


def _extract_ws_token(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return websocket.query_params.get("token")


def verify_ws_token(websocket: WebSocket) -> bool:
    """Return True when the WebSocket may proceed (no auth configured or token matches)."""
    settings = get_settings()
    expected = settings.api_token.strip()
    if not expected:
        return True
    provided = _extract_ws_token(websocket)
    return provided == expected


class ApiTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        expected = settings.api_token.strip()
        if not expected or _is_public(request.url.path):
            return await call_next(request)

        provided = _extract_token(request)
        if provided != expected:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API token"})
        return await call_next(request)
