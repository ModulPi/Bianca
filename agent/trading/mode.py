from __future__ import annotations

from agent.config import get_settings
from agent.storage.repository import AgentConfigRepository

_MODE_KEY = "trading_mode"


async def get_trading_mode() -> str:
    repo = AgentConfigRepository()
    stored = await repo.get_config_value(_MODE_KEY)
    if stored in {"demo", "live"}:
        return stored
    return get_settings().trading_mode


async def set_trading_mode(mode: str) -> None:
    if mode not in {"demo", "live"}:
        raise ValueError("mode must be demo or live")
    if mode == "live":
        cfg = get_settings()
        if not cfg.live_trading_confirmed:
            raise PermissionError(
                "实盘切换被拒绝：请先在 .env 设置 LIVE_TRADING_CONFIRMED=true 并完成模拟验证"
            )
        from agent.validation.paper_gate import evaluate_validation

        result = await evaluate_validation()
        if not result.get("can_enable_live"):
            reasons = result.get("reasons") or ["模拟验证未通过"]
            raise PermissionError("; ".join(reasons))
    await AgentConfigRepository().set_config_value(_MODE_KEY, mode)
