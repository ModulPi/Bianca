-- Bianca 002 MVP PostgreSQL 兼容 DDL
-- 在 002 结构基础上保留 PoC 代码所需字段；SQLite 仍用 001 + SQLAlchemy create_all

DO $ts$ BEGIN CREATE EXTENSION IF NOT EXISTS timescaledb; EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'timescaledb extension skipped: %', SQLERRM; END $ts$;

-- ============================================================
-- 1. 策略
-- ============================================================
CREATE TABLE IF NOT EXISTS strategies (
    id              TEXT            PRIMARY KEY,
    name            TEXT            NOT NULL,
    type            TEXT            NOT NULL,
    market          TEXT            NOT NULL DEFAULT 'spot',
    execution_mode  TEXT            NOT NULL DEFAULT 'auto',
    params_json     JSONB           NOT NULL DEFAULT '{}',
    state_json      JSONB           NOT NULL DEFAULT '{}',
    status          TEXT            NOT NULL DEFAULT 'created',
    created_at      TEXT            NOT NULL,
    updated_at      TEXT            NOT NULL,
    started_at      TEXT,
    stopped_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status);

-- ============================================================
-- 2. 交易记录（002 核心字段 + PoC 扩展）
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_logs (
    id                TEXT            PRIMARY KEY,
    strategy_id       TEXT            REFERENCES strategies(id) ON DELETE SET NULL,
    strategy_name     TEXT,
    symbol            TEXT            NOT NULL,
    side              TEXT            NOT NULL,
    quantity          DOUBLE PRECISION,
    price             DOUBLE PRECISION,
    order_type        TEXT,
    llm_confidence    DOUBLE PRECISION,
    decision_reason   TEXT            NOT NULL DEFAULT '',
    risk_decision     TEXT,
    risk_reason       TEXT,
    external_order_id TEXT,
    decision_id       TEXT,
    execution_mode    TEXT,
    status            TEXT            NOT NULL,
    created_at        TEXT            NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_logs_created ON trade_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_logs_side_status ON trade_logs(side, status);
CREATE INDEX IF NOT EXISTS idx_trade_logs_strategy_time ON trade_logs(strategy_id, created_at DESC);

-- ============================================================
-- 3. 持仓（002）
-- ============================================================
CREATE TABLE IF NOT EXISTS positions (
    id              TEXT            PRIMARY KEY,
    strategy_id     TEXT            NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    symbol          TEXT            NOT NULL,
    market          TEXT            NOT NULL DEFAULT 'spot',
    quantity        DOUBLE PRECISION NOT NULL DEFAULT 0,
    entry_price     DOUBLE PRECISION NOT NULL DEFAULT 0,
    current_price   DOUBLE PRECISION,
    unrealized_pnl  DOUBLE PRECISION DEFAULT 0,
    realized_pnl    DOUBLE PRECISION DEFAULT 0,
    leverage        INTEGER         DEFAULT 1,
    created_at      TEXT            NOT NULL,
    updated_at      TEXT            NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_strategy_symbol ON positions(strategy_id, symbol);

-- ============================================================
-- 4. 风控事件
-- ============================================================
CREATE TABLE IF NOT EXISTS risk_events (
    id                  TEXT            PRIMARY KEY,
    event_type          TEXT            NOT NULL,
    detail              JSONB           NOT NULL DEFAULT '{}',
    related_trade_id    TEXT            REFERENCES trade_logs(id) ON DELETE SET NULL,
    related_strategy_id TEXT            REFERENCES strategies(id) ON DELETE SET NULL,
    created_at          TEXT            NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_risk_events_time ON risk_events(created_at DESC);

-- ============================================================
-- 5. LLM 决策（PoC 保留）
-- ============================================================
CREATE TABLE IF NOT EXISTS decision_logs (
    id                TEXT            PRIMARY KEY,
    model_used        TEXT            NOT NULL,
    prompt_summary    TEXT,
    raw_output        TEXT            NOT NULL,
    parsed_signal     JSONB           NOT NULL DEFAULT '{}',
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    created_at        TEXT            NOT NULL
);

-- ============================================================
-- 6. Agent 配置（PoC 保留）
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_config (
    key               TEXT            PRIMARY KEY,
    value             TEXT            NOT NULL
);

-- ============================================================
-- 7. 会话汇总
-- ============================================================
CREATE TABLE IF NOT EXISTS session_summaries (
    id              TEXT            PRIMARY KEY,
    started_at      TEXT            NOT NULL,
    ended_at        TEXT,
    tick_count      INTEGER         NOT NULL DEFAULT 0,
    trading_style   TEXT            NOT NULL DEFAULT 'conservative',
    usage_json      JSONB           NOT NULL DEFAULT '{}',
    trades_json     JSONB           NOT NULL DEFAULT '{}',
    pnl_json        JSONB           NOT NULL DEFAULT '{}',
    positions_json  JSONB           NOT NULL DEFAULT '{}',
    loop_closed     INTEGER         NOT NULL DEFAULT 0,
    created_at      TEXT            NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_started ON session_summaries(started_at DESC);

-- ============================================================
-- 8. 半自动待确认
-- ============================================================
CREATE TABLE IF NOT EXISTS pending_signals (
    id                TEXT            PRIMARY KEY,
    strategy_id       TEXT            REFERENCES strategies(id) ON DELETE SET NULL,
    signal_json       JSONB           NOT NULL,
    market_data_json  JSONB           NOT NULL DEFAULT '{}',
    decision_id       TEXT,
    session_id        TEXT,
    status            TEXT            NOT NULL DEFAULT 'pending',
    expires_at        TEXT            NOT NULL,
    created_at        TEXT            NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_signals_status ON pending_signals(status, expires_at);

-- ============================================================
-- 9. 模拟验证
-- ============================================================
CREATE TABLE IF NOT EXISTS paper_validations (
    id              TEXT            PRIMARY KEY,
    strategy_id     TEXT            REFERENCES strategies(id) ON DELETE SET NULL,
    started_at      TEXT            NOT NULL,
    validated_at    TEXT,
    status          TEXT            NOT NULL DEFAULT 'running',
    metrics_json    JSONB           NOT NULL DEFAULT '{}',
    created_at      TEXT            NOT NULL
);

-- ============================================================
-- 10. AI 分析报告（002）
-- ============================================================
CREATE TABLE IF NOT EXISTS analysis_reports (
    id          TEXT            PRIMARY KEY,
    model_used  TEXT            NOT NULL,
    content     TEXT            NOT NULL,
    suggestions JSONB           DEFAULT '[]',
    confidence  DOUBLE PRECISION,
    symbols     TEXT            NOT NULL DEFAULT '',
    created_at  TEXT            NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analysis_reports_time ON analysis_reports(created_at DESC);

-- ============================================================
-- 11. K 线 hypertable（TimescaleDB）
-- ============================================================
CREATE TABLE IF NOT EXISTS klines (
    time        TIMESTAMPTZ         NOT NULL,
    symbol      TEXT                NOT NULL,
    interval    TEXT                NOT NULL DEFAULT '1m',
    open        DOUBLE PRECISION    NOT NULL,
    high        DOUBLE PRECISION    NOT NULL,
    low         DOUBLE PRECISION    NOT NULL,
    close       DOUBLE PRECISION    NOT NULL,
    volume      DOUBLE PRECISION    NOT NULL DEFAULT 0,
    trades      INTEGER
);

DO $ts$ BEGIN IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'create_hypertable') THEN PERFORM create_hypertable('klines', 'time', if_not_exists => TRUE); END IF; EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'klines hypertable skipped: %', SQLERRM; END $ts$;

CREATE INDEX IF NOT EXISTS idx_klines_symbol_interval_time ON klines(symbol, interval, time DESC);

-- Agent 默认策略（无 strategy_id 的 Agent 交易挂靠此记录）
INSERT INTO strategies (
    id, name, type, market, execution_mode, params_json, state_json,
    status, created_at, updated_at
) VALUES (
    '00000000-0000-4000-8000-000000000001',
    'Agent Default',
    'trend',
    'spot',
    'auto',
    '{}',
    '{}',
    'running',
    '1970-01-01T00:00:00+00:00',
    '1970-01-01T00:00:00+00:00'
) ON CONFLICT (id) DO NOTHING;
