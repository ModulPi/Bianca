from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.api.ws_manager import ws_manager
from agent.security.auth import verify_ws_token

router = APIRouter()


@router.websocket("/ws/system")
async def ws_system(websocket: WebSocket) -> None:
    if not verify_ws_token(websocket):
        await websocket.close(code=1008, reason="Invalid or missing API token")
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
