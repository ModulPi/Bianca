from __future__ import annotations

from agent.dashboard.cache import clear_snapshot_cache
from agent.dashboard.etag import clear_snapshot_store


def invalidate_dashboard_snapshot(*, exchange_cache: bool = False) -> None:
    """变更 Agent/交易/待确认状态后调用，避免 ETag 快速 304 返回陈旧 snapshot。"""
    clear_snapshot_store()
    if exchange_cache:
        clear_snapshot_cache()
