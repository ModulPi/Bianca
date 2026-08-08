from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.config import get_settings
from agent.dashboard.invalidate import invalidate_dashboard_snapshot
from agent.graph.command_agent import get_chat_messages, handle_chat_message
from agent.runner import get_runner

router = APIRouter(prefix="/agent", tags=["agent-chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class ChatActionItem(BaseModel):
    action: str
    symbol: str | None = None
    style: str | None = None


class ChatMessageItem(BaseModel):
    role: str
    content: str
    intent: str | None = None


class ChatResponse(BaseModel):
    intent: str
    symbol: str | None = None
    reply: str
    actions: list[ChatActionItem] = []
    messages: list[ChatMessageItem] = []


@router.post("/chat", response_model=ChatResponse)
async def agent_chat(body: ChatRequest) -> ChatResponse:
    settings = get_settings()
    session_id = body.session_id
    if not session_id:
        snap = await get_runner().get_snapshot()
        session_id = snap.session_id

    try:
        result = await handle_chat_message(body.message, session_id=session_id, settings=settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    invalidate_dashboard_snapshot()
    return ChatResponse(
        intent=result.get("intent", "unknown"),
        symbol=result.get("symbol"),
        reply=result.get("reply", ""),
        actions=[ChatActionItem(**a) for a in result.get("actions", [])],
        messages=[
            ChatMessageItem(
                role=m.get("role", "user"),
                content=m.get("content", ""),
                intent=m.get("intent"),
            )
            for m in result.get("messages", [])
        ],
    )


@router.get("/chat/history", response_model=ChatResponse)
async def agent_chat_history() -> ChatResponse:
    items = await get_chat_messages(limit=50)
    return ChatResponse(
        intent="history",
        reply="",
        messages=[
            ChatMessageItem(
                role=m.get("role", "user"),
                content=m.get("content", ""),
                intent=m.get("intent"),
            )
            for m in items
        ],
    )
