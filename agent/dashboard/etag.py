from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass

from agent.api.schemas import DashboardSnapshotResponse

_ETAG_RE = re.compile(r'^W/"|"|\s', re.IGNORECASE)


def normalize_etag(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _ETAG_RE.sub("", value.strip())
    return cleaned or None


def format_etag(digest: str) -> str:
    return f'"{digest}"'


def snapshot_etag(snap: DashboardSnapshotResponse) -> str:
    payload = snap.model_dump(mode="json")
    payload.pop("generated_at", None)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def etag_matches(header_value: str | None, etag: str) -> bool:
    if not header_value:
        return False
    incoming = normalize_etag(header_value)
    return incoming == etag if incoming else False


@dataclass
class _SnapshotStore:
    etag: str
    snapshot: DashboardSnapshotResponse
    built_at: float


_store: _SnapshotStore | None = None
_STORE_TTL_SECONDS = 5.0


def clear_snapshot_store() -> None:
    global _store
    _store = None


def try_fast_not_modified(if_none_match: str | None) -> str | None:
    """在 TTL 内若 ETag 未变，跳过重算 snapshot（仅返回 ETag）。"""
    global _store
    if _store is None or not if_none_match:
        return None
    if time.monotonic() - _store.built_at > _STORE_TTL_SECONDS:
        return None
    if etag_matches(if_none_match, _store.etag):
        return _store.etag
    return None


def remember_snapshot(snap: DashboardSnapshotResponse, etag: str) -> None:
    global _store
    _store = _SnapshotStore(etag=etag, snapshot=snap, built_at=time.monotonic())
