from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from agent.config import Settings, get_settings
from agent.storage.repository import PaperValidationRepository

logger = logging.getLogger(__name__)


def _parse_ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _session_hours(started: str, ended: str | None) -> float:
    end = _parse_ts(ended) if ended else datetime.now(UTC)
    start = _parse_ts(started)
    return max((end - start).total_seconds() / 3600, 0.0)


async def ensure_validation_running(*, settings: Settings | None = None) -> None:
    cfg = settings or get_settings()
    repo = PaperValidationRepository()
    current = await repo.get_active()
    if current is None:
        await repo.create(started_at=datetime.now(UTC).isoformat())


async def record_session_for_validation(summary: dict, *, settings: Settings | None = None) -> dict:
    cfg = settings or get_settings()
    await ensure_validation_running(settings=cfg)
    repo = PaperValidationRepository()
    row = await repo.get_active()
    if row is None:
        return {"status": "none"}

    metrics = json.loads(row.metrics_json)
    hours = _session_hours(summary["started_at"], summary.get("ended_at"))
    metrics["cumulative_hours"] = round(metrics.get("cumulative_hours", 0) + hours, 4)
    metrics["sessions"] = metrics.get("sessions", 0) + 1
    if summary.get("trades", {}).get("loop_closed"):
        metrics["loop_closed_sessions"] = metrics.get("loop_closed_sessions", 0) + 1
    metrics["buy_filled_total"] = metrics.get("buy_filled_total", 0) + summary.get("trades", {}).get(
        "buy_filled", 0
    )
    metrics["sell_filled_total"] = metrics.get("sell_filled_total", 0) + summary.get("trades", {}).get(
        "sell_filled", 0
    )

    await repo.update_metrics(row.id, metrics)
    return await evaluate_validation(settings=cfg)


async def evaluate_validation(*, settings: Settings | None = None) -> dict:
    cfg = settings or get_settings()
    repo = PaperValidationRepository()
    row = await repo.get_active()
    if row is None:
        return {"status": "none", "can_enable_live": False, "metrics": {}}

    metrics = json.loads(row.metrics_json)
    min_h = cfg.paper_validation_min_hours
    hours_ok = metrics.get("cumulative_hours", 0) >= min_h
    loop_ok = not cfg.paper_validation_require_loop or metrics.get("loop_closed_sessions", 0) >= 1
    trades_ok = metrics.get("buy_filled_total", 0) >= 1 and metrics.get("sell_filled_total", 0) >= 1
    passed = hours_ok and loop_ok and trades_ok

    reasons: list[str] = []
    if not hours_ok:
        reasons.append(f"累计模拟时长 {metrics.get('cumulative_hours', 0):.1f}h < {min_h}h")
    if not loop_ok:
        reasons.append("尚未完成闭环会话")
    if not trades_ok:
        reasons.append("累计 filled BUY/SELL 不足")

    if passed and row.status != "passed":
        await repo.mark_passed(row.id)
        status = "passed"
    elif not passed:
        status = "running"
    else:
        status = row.status

    return {
        "id": row.id,
        "status": status,
        "can_enable_live": passed,
        "metrics": metrics,
        "requirements": {
            "min_hours": min_h,
            "require_loop": cfg.paper_validation_require_loop,
        },
        "reasons": reasons,
        "started_at": row.started_at,
        "validated_at": row.validated_at,
    }


async def assert_demo_mode_for_trading(*, settings: Settings | None = None) -> None:
    from agent.trading.mode import get_trading_mode

    mode = await get_trading_mode()
    if mode != "live":
        return
    result = await evaluate_validation(settings=settings or get_settings())
    if not result.get("can_enable_live"):
        raise PermissionError("模拟验证未通过，无法使用 live 模式: " + "; ".join(result.get("reasons") or []))
