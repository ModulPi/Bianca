from __future__ import annotations

import logging
from typing import Any

from agent.config import Settings, get_settings

logger = logging.getLogger(__name__)

_redis_client: Any | None = None
_memory_store: dict[str, str] = {}


def _redis_configured(settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    return bool(cfg.redis_url.strip())


async def init_redis(*, settings: Settings | None = None) -> None:
    global _redis_client
    cfg = settings or get_settings()
    if not _redis_configured(cfg):
        logger.info("Redis 未配置，使用进程内内存缓存")
        return
    try:
        from redis.asyncio import Redis

        _redis_client = Redis.from_url(cfg.redis_url, decode_responses=True)
        await _redis_client.ping()
        logger.info("Redis 已连接")
    except Exception:  # noqa: BLE001
        logger.exception("Redis 连接失败，降级为内存缓存")
        _redis_client = None


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def redis_health() -> dict[str, str]:
    if not _redis_configured():
        return {"status": "not_configured", "detail": "未设置 REDIS_URL"}
    if _redis_client is None:
        return {"status": "memory", "detail": "Redis 不可用，使用内存缓存"}
    try:
        await _redis_client.ping()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


async def cache_set(key: str, value: str, *, ttl_seconds: int | None = None) -> None:
    if _redis_client is not None:
        if ttl_seconds:
            await _redis_client.set(key, value, ex=ttl_seconds)
        else:
            await _redis_client.set(key, value)
        return
    _memory_store[key] = value


async def cache_get(key: str) -> str | None:
    if _redis_client is not None:
        return await _redis_client.get(key)
    return _memory_store.get(key)


async def cache_delete(key: str) -> None:
    if _redis_client is not None:
        await _redis_client.delete(key)
        return
    _memory_store.pop(key, None)


def session_cache_key(session_id: str) -> str:
    return f"bianca:session:{session_id}"


async def set_active_session(session_id: str, started_at: str) -> None:
    await cache_set(session_cache_key(session_id), started_at, ttl_seconds=86400)


async def get_active_session(session_id: str) -> str | None:
    return await cache_get(session_cache_key(session_id))


async def clear_active_session(session_id: str) -> None:
    await cache_delete(session_cache_key(session_id))
