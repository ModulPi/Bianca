from __future__ import annotations

import logging

import httpx

from agent.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def send_telegram(text: str, *, settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    if not cfg.telegram_configured:
        logger.debug("Telegram not configured, skip notify")
        return False
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": cfg.telegram_chat_id,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Telegram send failed")
        return False


def format_session_summary(summary: dict) -> str:
    trades = summary.get("trades") or {}
    pnl = summary.get("pnl") or {}
    usage = summary.get("usage") or {}
    loop = "✅" if trades.get("loop_closed") else "❌"
    return (
        f"Bianca 会话结束\n"
        f"session: {summary.get('session_id', '')[:8]}…\n"
        f"闭环 {loop} · BUY/SELL filled: {trades.get('buy_filled', 0)}/{trades.get('sell_filled', 0)}\n"
        f"PnL 已实现: {pnl.get('realized_usdt', 0):.4f} USDT\n"
        f"Token: {usage.get('total_tokens', 0)} · ticks: {summary.get('agent', {}).get('tick_count', 0)}"
    )


async def notify_session_closed(summary: dict, *, settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    if not cfg.notify_on_session_close:
        return False
    return await send_telegram(format_session_summary(summary), settings=cfg)


async def notify_risk_reject(reason: str, signal: dict | None = None) -> dict[str, bool]:
    from agent.notify.email import notify_all

    cfg = get_settings()
    if not cfg.notify_on_risk_reject:
        return {"telegram": False, "email": False}
    action = (signal or {}).get("action", "?")
    return await notify_all(
        "Bianca 风控拒绝",
        f"动作: {action}\n原因: {reason}",
        settings=cfg,
    )


async def notify_daily_digest(text: str) -> bool:
    return await send_telegram(f"Bianca 日摘要\n{text}")
