from __future__ import annotations

import logging
from typing import Literal

from agent.config import Settings, get_settings
from agent.storage.repository import AgentConfigRepository

logger = logging.getLogger(__name__)

_DEGRADED_KEY = "agent_degraded"
_FAILURE_PREFIX = "worker_failures:"


async def is_degraded() -> bool:
    repo = AgentConfigRepository()
    row = await repo.get_config_value(_DEGRADED_KEY)
    return row == "1"


async def get_effective_execution_mode(
    settings: Settings | None = None,
) -> Literal["auto", "semi_auto", "signal_only"]:
    cfg = settings or get_settings()
    if cfg.execution_mode:
        base = cfg.execution_mode
    elif cfg.resolved_execution_mode == "semi_auto":
        base = "semi_auto"
    else:
        base = cfg.resolved_execution_mode
    if await is_degraded() and base == "auto":
        return "semi_auto"
    return base


async def record_tick_success(symbol: str) -> None:
    repo = AgentConfigRepository()
    await repo.set_config_value(f"{_FAILURE_PREFIX}{symbol}", "0")


async def record_tick_failure(symbol: str, error: str, *, settings: Settings | None = None) -> bool:
    """记录 Worker 失败；返回是否触发了自动降级。"""
    cfg = settings or get_settings()
    if not cfg.auto_degrade_enabled:
        return False
    repo = AgentConfigRepository()
    key = f"{_FAILURE_PREFIX}{symbol}"
    current = int((await repo.get_config_value(key)) or "0")
    current += 1
    await repo.set_config_value(key, str(current))
    if current < cfg.auto_degrade_failures:
        return False
    if await is_degraded():
        return False
    await repo.set_config_value(_DEGRADED_KEY, "1")
    logger.warning(
        "Agent 自动降级：%s 连续失败 %s 次，切换 semi_auto 等待人工确认",
        symbol,
        current,
    )
    from agent.notify.email import notify_all

    await notify_all(
        "Bianca Agent 自动降级",
        f"Worker {symbol} 连续失败 {current} 次\n最近错误: {error}\n已切换 semi_auto，请人工确认或调用 POST /agent/recover",
        settings=cfg,
    )
    return True


async def clear_degradation() -> None:
    repo = AgentConfigRepository()
    await repo.set_config_value(_DEGRADED_KEY, "0")
