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
from agent.exchange.futures_coin_demo import FuturesCoinDemoExchange, check_futures_coin
from agent.exchange.futures_demo import FuturesDemoExchange, check_futures_demo
from agent.notify.email import send_email
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
        email_configured=cfg.email_configured,
        notify_on_session_close=cfg.notify_on_session_close,
        notify_on_risk_reject=cfg.notify_on_risk_reject,
    )


@notify_router.post("/test", response_model=MessageResponse)
async def notify_test() -> MessageResponse:
    cfg = get_settings()
    if not cfg.telegram_configured and not cfg.email_configured:
        raise HTTPException(
            status_code=400,
            detail="未配置 Telegram 或 SMTP（TELEGRAM_* / SMTP_* / NOTIFY_EMAIL_TO）",
        )
    sent: list[str] = []
    if cfg.telegram_configured:
        if await send_telegram("Bianca 测试通知 — Telegram 通道正常"):
            sent.append("telegram")
    if cfg.email_configured:
        if await send_email("Bianca 测试通知", "Email 通道正常"):
            sent.append("email")
    if not sent:
        raise HTTPException(status_code=502, detail="通知发送失败")
    return MessageResponse(message=f"Test sent via: {', '.join(sent)}")


@notify_router.post("/daily-digest", response_model=MessageResponse)
async def notify_daily() -> MessageResponse:
    cfg = get_settings()
    if not cfg.telegram_configured and not cfg.email_configured:
        raise HTTPException(status_code=400, detail="未配置通知通道")
    text = await build_daily_summary_text()
    sent: list[str] = []
    if cfg.telegram_configured:
        if await notify_daily_digest(text):
            sent.append("telegram")
    if cfg.email_configured:
        if await send_email("Bianca 日摘要", text):
            sent.append("email")
    if not sent:
        raise HTTPException(status_code=502, detail="通知发送失败")
    return MessageResponse(message=f"Daily digest sent via: {', '.join(sent)}")


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
    u_probe = await check_futures_demo(settings=cfg, live=False)
    coin_probe = await check_futures_coin(settings=cfg, live=False)
    if not cfg.futures_enabled:
        return FuturesStatusResponse(
            enabled=False,
            message="合约 API 未启用（FUTURES_ENABLED=false）",
            connectivity=u_probe["status"],
            detail=u_probe.get("detail"),
            futures_u=u_probe,
            futures_coin=coin_probe,
        )
    return FuturesStatusResponse(
        enabled=True,
        message="U 本位 + 币本位合约 Demo 已启用",
        connectivity=u_probe["status"],
        detail=u_probe.get("detail"),
        futures_u=u_probe,
        futures_coin=coin_probe,
    )


@futures_router.get("/balance")
async def futures_balance(market: str = "futures_u") -> dict:
    cfg = get_settings()
    if not cfg.futures_enabled:
        raise HTTPException(status_code=400, detail="FUTURES_ENABLED=false")
    from agent.trading.mode import get_trading_mode

    live = await get_trading_mode() == "live"
    if market == "futures_coin":
        async with FuturesCoinDemoExchange(cfg, live=live) as demo:
            return await demo.fetch_balance()
    async with FuturesDemoExchange(cfg, live=live) as demo:
        return await demo.fetch_balance()
