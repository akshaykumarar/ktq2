-- Step 3 Database Migration: Decision Analyst, Scenarios, Feedback, Audit Log, and Observability
-- Schema: ktq (or dynamically applied)

CREATE TABLE IF NOT EXISTS ktq.analyst_traces (
    trace_id VARCHAR(64) PRIMARY KEY,
    rfx_id BIGINT REFERENCES ktq.rfx(id) ON DELETE CASCADE,
    session_id VARCHAR(128) NOT NULL,
    question TEXT NOT NULL,
    tool_steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    sql_executed JSONB NOT NULL DEFAULT '[]'::jsonb,
    path_used VARCHAR(32) NOT NULL DEFAULT 'fallback', -- wren | fallback
    latencies JSONB NOT NULL DEFAULT '{}'::jsonb,
    tokens JSONB NOT NULL DEFAULT '{}'::jsonb,
    final_response JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence VARCHAR(32) NOT NULL DEFAULT 'high', -- high | medium | low
    confidence_reason TEXT,
    prompt_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    semantic_model_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ktq.analyst_feedback (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(64) REFERENCES ktq.analyst_traces(trace_id) ON DELETE CASCADE,
    rating INTEGER NOT NULL, -- 1 (up) or -1 (down), or 1-5
    comment TEXT,
    corrected_sql TEXT,
    corrected_answer TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ktq.award_scenarios (
    id BIGSERIAL PRIMARY KEY,
    rfx_id BIGINT NOT NULL REFERENCES ktq.rfx(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL DEFAULT 'Scenario',
    constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
    allocation JSONB NOT NULL DEFAULT '[]'::jsonb,
    totals JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'draft', -- draft | finalized
    accepted_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finalized_at TIMESTAMPTZ,
    finalized_by VARCHAR(128)
);

CREATE TABLE IF NOT EXISTS ktq.analyst_audit_log (
    id BIGSERIAL PRIMARY KEY,
    rfx_id BIGINT NOT NULL REFERENCES ktq.rfx(id) ON DELETE CASCADE,
    scenario_id BIGINT REFERENCES ktq.award_scenarios(id) ON DELETE SET NULL,
    action VARCHAR(64) NOT NULL,
    actor VARCHAR(128) NOT NULL DEFAULT 'buyer',
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analyst_traces_rfx ON ktq.analyst_traces(rfx_id);
CREATE INDEX IF NOT EXISTS idx_analyst_traces_session ON ktq.analyst_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_award_scenarios_rfx ON ktq.award_scenarios(rfx_id);
