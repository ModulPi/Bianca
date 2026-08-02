-- ============================================================
-- Bianca — PoC SQLite DDL
-- 版本: v0.3 | 日期: 2026-07-31
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS trade_logs (
    id                TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'HOLD')),
    quantity          REAL,
    price             REAL,
    order_type        TEXT CHECK (order_type IN ('MARKET', 'LIMIT')),
    llm_confidence    REAL,
    decision_reason   TEXT NOT NULL,
    risk_decision     TEXT CHECK (risk_decision IN ('approved', 'rejected', 'skipped')),
    risk_reason       TEXT,
    external_order_id TEXT,
    status            TEXT NOT NULL CHECK (status IN ('signal_only', 'submitted', 'filled', 'failed')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trade_logs_created ON trade_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_logs_side_status ON trade_logs(side, status);

CREATE TABLE IF NOT EXISTS risk_events (
    id                TEXT PRIMARY KEY,
    event_type        TEXT NOT NULL CHECK (event_type IN ('max_trade_amount', 'daily_loss')),
    detail            TEXT NOT NULL DEFAULT '{}',
    related_trade_id  TEXT REFERENCES trade_logs(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_risk_events_time ON risk_events(created_at DESC);

CREATE TABLE IF NOT EXISTS decision_logs (
    id                TEXT PRIMARY KEY,
    model_used        TEXT NOT NULL,
    prompt_summary    TEXT,
    raw_output        TEXT NOT NULL,
    parsed_signal     TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
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
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
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
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pending_signals_status ON pending_signals(status, expires_at);

CREATE TABLE IF NOT EXISTS strategies (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('grid', 'dca', 'trend')),
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

COMMIT;
