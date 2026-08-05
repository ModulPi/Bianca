from agent.storage.database import _split_sql_statements


def test_split_sql_respects_dollar_quoted_do_block():
    raw = """
-- comment
CREATE TABLE foo (id INT);

DO $ts$ BEGIN
    IF TRUE THEN
        EXECUTE 'ALTER TABLE klines SET (timescaledb.compress)';
    END IF;
EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'skip';
END $ts$;

INSERT INTO bar VALUES (1);
"""
    parts = _split_sql_statements(raw)
    assert len(parts) == 3
    assert parts[0].startswith("CREATE TABLE foo")
    assert "$ts$" in parts[1]
    assert "END $ts$;" in parts[1]
    assert parts[2].startswith("INSERT INTO bar")


def test_split_sql_nested_dollar_tags():
    raw = """
DO $ts$ BEGIN
    EXECUTE $mv$
        SELECT 1;
    $mv$;
END $ts$;
"""
    parts = _split_sql_statements(raw)
    assert len(parts) == 1
    assert parts[0].count("$mv$") == 2
