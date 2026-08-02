from __future__ import annotations

import json
from typing import Any


def parse_json_field(value: Any, *, default: Any | None = None) -> Any:
    """兼容 SQLite TEXT 与 PostgreSQL JSONB（asyncpg 可能返回 dict）。"""
    if value is None:
        return default if default is not None else {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        if not value.strip():
            return default if default is not None else {}
        return json.loads(value)
    return value


def dump_json_field(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
