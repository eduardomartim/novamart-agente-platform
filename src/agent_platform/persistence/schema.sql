-- Agent Platform storage schema (SQLite, demo profile).
--
-- Design notes:
--  * No table stores credentials. Payload columns hold JSON that has already
--    passed through security.sanitization.sanitize().
--  * Monetary amounts are stored as INTEGER nano-USD rather than REAL. Costs
--    accumulate across thousands of rows and binary floats would drift; integer
--    nano-USD keeps SUM() exact and still resolves sub-cent model pricing.
--  * Timestamps are ISO-8601 UTC strings, which sort lexicographically.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS requests (
    request_id       TEXT PRIMARY KEY,
    trace_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    completed_at     TEXT,
    input_digest     TEXT NOT NULL,
    input_chars      INTEGER NOT NULL DEFAULT 0,
    route            TEXT,
    status           TEXT NOT NULL,
    blocked          INTEGER NOT NULL DEFAULT 0,
    block_reason     TEXT,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    latency_ms       REAL NOT NULL DEFAULT 0,
    provider         TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests (created_at);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests (status);

CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       TEXT NOT NULL,
    trace_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    sequence         INTEGER NOT NULL,
    agent            TEXT,
    event_type       TEXT NOT NULL,
    tool             TEXT,
    status           TEXT NOT NULL,
    policy_decision  TEXT,
    risk_level       TEXT,
    rule_ids         TEXT,
    latency_ms       REAL NOT NULL DEFAULT 0,
    error            TEXT,
    payload          TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_request ON events (request_id, sequence);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events (created_at);

CREATE TABLE IF NOT EXISTS llm_calls (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       TEXT NOT NULL,
    trace_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    agent            TEXT,
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    total_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_nano_usd    INTEGER NOT NULL DEFAULT 0,
    currency         TEXT NOT NULL DEFAULT 'USD',
    estimated        INTEGER NOT NULL DEFAULT 1,
    latency_ms       REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at ON llm_calls (created_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_request ON llm_calls (request_id);

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id           TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    dataset          TEXT NOT NULL,
    case_count       INTEGER NOT NULL DEFAULT 0,
    passed_count     INTEGER NOT NULL DEFAULT 0,
    correctness      REAL,
    safety           REAL,
    tool_accuracy    REAL,
    relevance        REAL,
    overall          REAL,
    judge_used       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_created_at ON eval_runs (created_at);

CREATE TABLE IF NOT EXISTS eval_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    case_id          TEXT NOT NULL,
    category         TEXT NOT NULL,
    passed           INTEGER NOT NULL DEFAULT 0,
    correctness      REAL,
    safety           REAL,
    tool_accuracy    REAL,
    relevance        REAL,
    overall          REAL,
    failure_reasons  TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES eval_runs (run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results (run_id);

CREATE TABLE IF NOT EXISTS drift_baselines (
    name             TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    source_run_id    TEXT,
    metrics          TEXT NOT NULL
);
