import pytest

from agent.storage.database import _PG_DDL_PATH, is_postgres_url, is_sqlite_url, schema_mode
from agent.storage.json_utils import parse_json_field


def test_pg_ddl_file_exists():
    assert _PG_DDL_PATH.is_file()
    content = _PG_DDL_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS strategies" in content
    assert "create_hypertable" in content
    assert "add_compression_policy" in content
    assert "add_retention_policy" in content
    assert "klines_5m" in content
    assert "timescaledb.continuous" in content
    assert "analysis_reports" in content
    assert "positions" in content


def test_parse_json_field_text_and_dict():
    assert parse_json_field('{"a": 1}') == {"a": 1}
    assert parse_json_field({"a": 1}) == {"a": 1}
    assert parse_json_field(None) == {}


def test_schema_mode_sqlite_default():
    assert is_sqlite_url("sqlite+aiosqlite:///./data/bianca.db")
    assert is_postgres_url("postgresql+asyncpg://localhost/bianca")
