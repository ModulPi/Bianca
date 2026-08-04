from __future__ import annotations

import threading
import time
from typing import Any

from agent.llm.prompts import normalize_symbol

_lock = threading.Lock()
_cache: dict[str, tuple[dict[str, Any], float]] = {}


def _cache_key(symbol: str) -> str:
    return normalize_symbol(symbol)


def set_ticker(symbol: str, ticker: dict[str, Any]) -> None:
    key = _cache_key(symbol)
    with _lock:
        _cache[key] = (dict(ticker), time.monotonic())


def get_fresh_ticker(symbol: str, max_age_seconds: float) -> dict[str, Any] | None:
    key = _cache_key(symbol)
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        data, updated_at = entry
        if time.monotonic() - updated_at > max_age_seconds:
            return None
        return dict(data)


def clear_ticker_cache() -> None:
    with _lock:
        _cache.clear()
