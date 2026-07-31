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

COMMIT;
