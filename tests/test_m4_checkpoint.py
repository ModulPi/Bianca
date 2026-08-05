from agent.checkpoint.store import checkpointer_backend, postgres_checkpointer_dsn
from agent.config import Settings, clear_settings_cache


def test_postgres_checkpointer_dsn():
    assert postgres_checkpointer_dsn("postgresql+asyncpg://u:p@localhost/bianca") == (
        "postgresql://u:p@localhost/bianca"
    )
    assert postgres_checkpointer_dsn("postgresql://u:p@localhost/bianca") == (
        "postgresql://u:p@localhost/bianca"
    )


def test_checkpointer_backend_selection():
    clear_settings_cache()
    sqlite_cfg = Settings(database_url="sqlite+aiosqlite:///./data/bianca.db")
    assert checkpointer_backend(sqlite_cfg) == "sqlite"

    pg_cfg = Settings(database_url="postgresql+asyncpg://localhost/bianca")
    assert checkpointer_backend(pg_cfg) == "postgresql"
