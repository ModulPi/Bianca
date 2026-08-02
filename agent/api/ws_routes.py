from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.api.ws_manager import ws_manager

router = APIRouter()


@router.websocket("/ws/system")
async def ws_system(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
