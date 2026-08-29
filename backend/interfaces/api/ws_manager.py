from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WsManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, ensure_ascii=False)
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_text(message)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                self._connections.discard(ws)


ws_manager = WsManager()
