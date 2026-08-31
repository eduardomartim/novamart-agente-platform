-- Agent Platform storage schema (PostgreSQL).
--
-- A translation of schema.sql, not a redesign. The two are kept deliberately
-- close so that the conformance suite -- which runs the same assertions against
-- both -- is comparing implementations rather than schemas.
--
-- Three choices are worth explaining, because each had a tempting alternative:
--
--  * Money stays INTEGER nano-USD, here BIGINT. Costs accumulate over thousands
--    of rows and binary floats drift; integer nano-USD keeps SUM() exact and
--    still resolves sub-cent model pricing. NUMERIC would also be exact, but it
--    would change arithmetic that is already correct and already tested.
--
--  * Timestamps stay TEXT holding ISO-8601 UTC, not TIMESTAMPTZ. `spend_since`
--    compares `created_at >= $1` against an ISO string, and with a fixed UTC
--    format lexicographic order is chronological order. Moving to TIMESTAMPTZ
--    would mean rewriting every comparison and introducing a timezone-
--    conversion boundary -- risk, for no behaviour the platform needs.
--
--  * `blocked` and `estimated` become BOOLEAN. This is the one place the native
--    type is plainly better, and the conversion is confined to the adapter.
--
-- No table stores credentials. Payload columns hold JSON that has already
-- passed through security.sanitization.

CREATE TABLE IF NOT EXISTS requests (
    request_id       TEXT PRIMARY KEY,
    trace_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    completed_at     TEXT,
    input_digest     TEXT NOT NULL,
    input_chars      INTEGER NOT NULL DEFAULT 0,
    route            TEXT,
    status           TEXT NOT NULL,
    blocked          BOOLEAN NOT NULL DEFAULT FALSE,
    block_reason     TEXT,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    latency_ms       DOUBLE PRECISION NOT NULL DEFAULT 0,
    provider         TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests (created_at);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests (status);

CREATE TABLE IF NOT EXISTS events (
    id               BIGSERIAL PRIMARY KEY,
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
    latency_ms       DOUBLE PRECISION NOT NULL DEFAULT 0,
    error            TEXT,
    payload          TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_request ON events (request_id, sequence);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events (created_at);

CREATE TABLE IF NOT EXISTS llm_calls (
    id               BIGSERIAL PRIMARY KEY,
    request_id       TEXT NOT NULL,
    trace_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    agent            TEXT,
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    total_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_nano_usd    BIGINT NOT NULL DEFAULT 0,
    currency         TEXT NOT NULL DEFAULT 'USD',
    estimated        BOOLEAN NOT NULL DEFAULT TRUE,
    latency_ms       DOUBLE PRECISION NOT NULL DEFAULT 0
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
    correctness      DOUBLE PRECISION,
    safety           DOUBLE PRECISION,
    tool_accuracy    DOUBLE PRECISION,
    relevance        DOUBLE PRECISION,
    overall          DOUBLE PRECISION,
    judge_used       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_created_at ON eval_runs (created_at);

CREATE TABLE IF NOT EXISTS eval_results (
    id               BIGSERIAL PRIMARY KEY,
    run_id           TEXT NOT NULL,
    case_id          TEXT NOT NULL,
    category         TEXT NOT NULL,
    passed           BOOLEAN NOT NULL DEFAULT FALSE,
    correctness      DOUBLE PRECISION,
    safety           DOUBLE PRECISION,
    tool_accuracy    DOUBLE PRECISION,
    relevance        DOUBLE PRECISION,
    overall          DOUBLE PRECISION,
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
