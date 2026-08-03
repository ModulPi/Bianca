from __future__ import annotations

import logging
from typing import Any

from agent.config import Settings, clear_settings_cache, set_effective_settings
from agent.security.crypto import decrypt_secret
from agent.storage.repository import ApiKeyRepository

logger = logging.getLogger(__name__)

# key_type -> Settings 字段映射说明（value 格式见 parse_secret_value）
_SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "binance": ("binance_api_key", "binance_api_secret"),
    "llm": ("llm_api_key",),
    "telegram": ("telegram_bot_token", "telegram_chat_id"),
}


def parse_secret_value(key_type: str, value: str) -> dict[str, str]:
    """解析 api_keys 存储格式。binance/telegram 为 `a:b` 两段式。"""
    text = value.strip()
    if key_type == "binance":
        if ":" not in text:
            raise ValueError("binance 密钥格式应为 API_KEY:API_SECRET")
        key, secret = text.split(":", 1)
        return {"binance_api_key": key.strip(), "binance_api_secret": secret.strip()}
    if key_type == "telegram":
        if ":" not in text:
            raise ValueError("telegram 格式应为 BOT_TOKEN:CHAT_ID")
        token, chat_id = text.split(":", 1)
        return {"telegram_bot_token": token.strip(), "telegram_chat_id": chat_id.strip()}
    if key_type == "llm":
        return {"llm_api_key": text}
    return {}


def _merge_patch(base: Settings, patch: dict[str, str]) -> Settings:
    """环境变量优先：仅当 env 字段为空时才用 DB 值填充。"""
    updates: dict[str, Any] = {}
    for field, value in patch.items():
        current = getattr(base, field, "")
        if isinstance(current, str) and current.strip():
            continue
        if value:
            updates[field] = value
    if not updates:
        return base
    return base.model_copy(update=updates)


async def build_secrets_patch(*, settings: Settings | None = None) -> dict[str, str]:
    cfg = settings if settings is not None else Settings()
    if not cfg.encryption_configured:
        return {}

    patch: dict[str, str] = {}
    repo = ApiKeyRepository()
    rows = await repo.list_all()
    seen_types: set[str] = set()
    for row in rows:
        if row.key_type in seen_types:
            continue
        seen_types.add(row.key_type)
        if row.key_type not in _SECRET_FIELDS:
            continue
        try:
            plain = decrypt_secret(row.encrypted_value, settings=cfg)
            parsed = parse_secret_value(row.key_type, plain)
            patch.update(parsed)
        except ValueError as exc:
            logger.warning("Skip api_key %s (%s): %s", row.id, row.key_type, exc)
    return patch


async def refresh_effective_settings(*, settings: Settings | None = None) -> Settings:
    """从 env + 加密 api_keys 表合并生成运行时 Settings。"""
    clear_settings_cache()
    base = Settings()
    patch = await build_secrets_patch(settings=base)
    effective = _merge_patch(base, patch)
    set_effective_settings(effective)
    if patch:
        logger.info("Runtime secrets loaded from api_keys (%d fields)", len(patch))
    return effective


async def reload_runtime_secrets() -> Settings:
    return await refresh_effective_settings()
