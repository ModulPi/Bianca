from __future__ import annotations

import logging

import httpx

from agent.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def send_email(
    subject: str,
    body: str,
    *,
    settings: Settings | None = None,
) -> bool:
    cfg = settings or get_settings()
    if not cfg.email_configured:
        logger.debug("Email not configured, skip notify")
        return False

    from_addr = cfg.notify_email_from.strip() or cfg.smtp_user.strip()
    to_addr = cfg.notify_email_to.strip()
    if not from_addr or not to_addr:
        return False

    message = (
        f"From: {from_addr}\r\n"
        f"To: {to_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"{body}"
    ).encode("utf-8")

    try:
        import smtplib

        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
            if cfg.smtp_use_tls:
                smtp.starttls()
            if cfg.smtp_user.strip():
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.sendmail(from_addr, [to_addr], message)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Email send failed")
        return False


async def notify_all(subject: str, body: str, *, settings: Settings | None = None) -> dict[str, bool]:
    from agent.notify.telegram import send_telegram

    cfg = settings or get_settings()
    results = {
        "telegram": False,
        "email": False,
    }
    if cfg.telegram_configured:
        results["telegram"] = await send_telegram(f"{subject}\n\n{body}", settings=cfg)
    if cfg.email_configured:
        results["email"] = await send_email(subject, body, settings=cfg)
    return results
