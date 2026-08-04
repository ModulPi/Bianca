from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

_store: dict[str, _CacheEntry] = {}


@dataclass
class _CacheEntry:
    value: object
    expires_at: float


def clear_snapshot_cache() -> None:
    """测试或运维用：清空看板 snapshot 进程内 TTL 缓存。"""
    _store.clear()


async def get_or_set(
    key: str,
    ttl_seconds: float,
    factory: Callable[[], Awaitable[T]],
) -> T:
    now = time.monotonic()
    entry = _store.get(key)
    if entry is not None and entry.expires_at > now:
        return entry.value  # type: ignore[return-value]
    value = await factory()
    _store[key] = _CacheEntry(value=value, expires_at=now + ttl_seconds)
    return value
