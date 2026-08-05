from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.api.schemas import (
    FuturesStatusResponse,
    MessageResponse,
    NotifyStatusResponse,
    TradingModeRequest,
    TradingModeResponse,
    ValidationStatusResponse,
)
from agent.config import get_settings
from agent.notify.telegram import notify_daily_digest, send_telegram
from agent.summary.aggregator import build_daily_summary_text
from agent.trading.mode import get_trading_mode, set_trading_mode
from agent.validation.paper_gate import evaluate_validation
from agent.storage.repository import PaperValidationRepository

router = APIRouter(prefix="/validation", tags=["validation"])


@router.get("/status", response_model=ValidationStatusResponse)
async def validation_status() -> ValidationStatusResponse:
    result = await evaluate_validation()
    mode = await get_trading_mode()
    cfg = get_settings()
    return ValidationStatusResponse(
        **result,
        trading_mode=mode,
        telegram_configured=cfg.telegram_configured,
        futures_enabled=cfg.futures_enabled,
    )


@router.post("/evaluate", response_model=ValidationStatusResponse)
async def validation_evaluate() -> ValidationStatusResponse:
    result = await evaluate_validation()
    mode = await get_trading_mode()
    cfg = get_settings()
    return ValidationStatusResponse(
        **result,
        trading_mode=mode,
        telegram_configured=cfg.telegram_configured,
        futures_enabled=cfg.futures_enabled,
    )


@router.post("/reset", response_model=MessageResponse)
async def validation_reset() -> MessageResponse:
    repo = PaperValidationRepository()
    row = await repo.reset()
    return MessageResponse(message=f"Paper validation reset: {row.id}")


notify_router = APIRouter(prefix="/notify", tags=["notify"])


@notify_router.get("/status", response_model=NotifyStatusResponse)
async def notify_status() -> NotifyStatusResponse:
    cfg = get_settings()
    return NotifyStatusResponse(
        telegram_configured=cfg.telegram_configured,
        notify_on_session_close=cfg.notify_on_session_close,
        notify_on_risk_reject=cfg.notify_on_risk_reject,
    )


@notify_router.post("/test", response_model=MessageResponse)
async def notify_test() -> MessageResponse:
    cfg = get_settings()
    if not cfg.telegram_configured:
        raise HTTPException(status_code=400, detail="Telegram 未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    ok = await send_telegram("Bianca 测试通知 — Telegram 通道正常")
    if not ok:
        raise HTTPException(status_code=502, detail="Telegram 发送失败，请检查 token/chat_id")
    return MessageResponse(message="Telegram test message sent")


@notify_router.post("/daily-digest", response_model=MessageResponse)
async def notify_daily() -> MessageResponse:
    cfg = get_settings()
    if not cfg.telegram_configured:
        raise HTTPException(status_code=400, detail="Telegram 未配置")
    text = await build_daily_summary_text()
    ok = await notify_daily_digest(text)
    if not ok:
        raise HTTPException(status_code=502, detail="Telegram 发送失败")
    return MessageResponse(message="Daily digest sent")


trading_router = APIRouter(prefix="/trading", tags=["trading"])


@trading_router.get("/mode", response_model=TradingModeResponse)
async def get_mode() -> TradingModeResponse:
    mode = await get_trading_mode()
    validation = await evaluate_validation()
    return TradingModeResponse(
        mode=mode,
        can_enable_live=bool(validation.get("can_enable_live")),
        validation_status=validation.get("status", "none"),
    )


@trading_router.post("/mode", response_model=TradingModeResponse)
async def post_mode(body: TradingModeRequest) -> TradingModeResponse:
    try:
        await set_trading_mode(body.mode)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    validation = await evaluate_validation()
    return TradingModeResponse(
        mode=body.mode,
        can_enable_live=bool(validation.get("can_enable_live")),
        validation_status=validation.get("status", "none"),
    )


futures_router = APIRouter(prefix="/futures", tags=["futures"])


@futures_router.get("/status", response_model=FuturesStatusResponse)
async def futures_status() -> FuturesStatusResponse:
    cfg = get_settings()
    return FuturesStatusResponse(
        enabled=cfg.futures_enabled,
        message="合约 API 尚未对接；MVP 仅支持 Demo 现货" if not cfg.futures_enabled else "合约 API 已启用",
    )
