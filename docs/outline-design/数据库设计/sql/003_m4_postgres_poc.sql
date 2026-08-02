# M4 — PoC 表结构 PostgreSQL 等价 DDL（与 SQLAlchemy models 对齐）
# TimescaleDB：启用扩展并预建 klines hypertable（业务写入后续迭代）

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS trade_logs (
    id                TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,
    quantity          DOUBLE PRECISION,
    price             DOUBLE PRECISION,
    order_type        TEXT,
    llm_confidence    DOUBLE PRECISION,
    decision_reason   TEXT NOT NULL,
    risk_decision     TEXT,
    risk_reason       TEXT,
    external_order_id TEXT,
    decision_id       TEXT,
    status            TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_logs_created ON trade_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_logs_side_status ON trade_logs(side, status);

CREATE TABLE IF NOT EXISTS risk_events (
    id                TEXT PRIMARY KEY,
    event_type        TEXT NOT NULL,
    detail            TEXT NOT NULL DEFAULT '{}',
    related_trade_id  TEXT REFERENCES trade_logs(id),
    created_at        TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);

CREATE INDEX IF NOT EXISTS idx_risk_events_time ON risk_events(created_at DESC);

CREATE TABLE IF NOT EXISTS decision_logs (
    id                TEXT PRIMARY KEY,
    model_used        TEXT NOT NULL,
    prompt_summary    TEXT,
    raw_output        TEXT NOT NULL,
    parsed_signal     TEXT NOT NULL DEFAULT '{}',
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    created_at        TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);

CREATE TABLE IF NOT EXISTS agent_config (
    key               TEXT PRIMARY KEY,
    value             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_summaries (
    id                TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    tick_count        INTEGER NOT NULL DEFAULT 0,
    trading_style     TEXT NOT NULL DEFAULT 'conservative',
    usage_json        TEXT NOT NULL DEFAULT '{}',
    trades_json       TEXT NOT NULL DEFAULT '{}',
    pnl_json          TEXT NOT NULL DEFAULT '{}',
    positions_json    TEXT NOT NULL DEFAULT '{}',
    loop_closed       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_started ON session_summaries(started_at DESC);

CREATE TABLE IF NOT EXISTS pending_signals (
    id                TEXT PRIMARY KEY,
    strategy_id       TEXT,
    signal_json       TEXT NOT NULL,
    market_data_json  TEXT NOT NULL,
    decision_id       TEXT,
    session_id        TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    expires_at        TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')::TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_signals_status ON pending_signals(status, expires_at);

CREATE TABLE IF NOT EXISTS strategies (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL,
    market            TEXT NOT NULL DEFAULT 'spot',
    execution_mode    TEXT NOT NULL DEFAULT 'auto',
    params_json       TEXT NOT NULL DEFAULT '{}',
    state_json        TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'created',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    started_at        TEXT,
    stopped_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status);

CREATE TABLE IF NOT EXISTS paper_validations (
    id                TEXT PRIMARY KEY,
    strategy_id       TEXT,
    started_at        TEXT NOT NULL,
    validated_at      TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    metrics_json      TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
);

-- TimescaleDB klines（M4 预建，写入路径待后续）
CREATE TABLE IF NOT EXISTS klines (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    interval    TEXT NOT NULL DEFAULT '1m',
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    volume      DOUBLE PRECISION NOT NULL DEFAULT 0
);

SELECT create_hypertable('klines', 'time', if_not_exists => TRUE);
