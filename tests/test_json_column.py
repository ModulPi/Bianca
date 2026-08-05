import json

import pytest

from agent.storage.json_column import JsonText

pytestmark = pytest.mark.no_db


def test_json_text_bind_postgresql():
    col = JsonText()
    dialect = type("D", (), {"name": "postgresql"})()
    bound = col.process_bind_param('{"a": 1}', dialect)
    assert bound == {"a": 1}


def test_json_text_bind_sqlite():
    col = JsonText()
    dialect = type("D", (), {"name": "sqlite"})()
    bound = col.process_bind_param({"a": 1}, dialect)
    assert bound == '{"a": 1}'


def test_json_text_result_postgresql():
    col = JsonText()
    dialect = type("D", (), {"name": "postgresql"})()
    assert col.process_result_value({"a": 1}, dialect) == '{"a": 1}'
