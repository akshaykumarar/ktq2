-- Migration 002: Vendor Input Intake, OCR Extraction, Flags, Crops, Views & Lifecycle
-- Dynamically templated with DB_SCHEMA

CREATE SCHEMA IF NOT EXISTS ktq;

-- 1. Vendors table
CREATE TABLE IF NOT EXISTS ktq.vendors (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    gstin TEXT,
    address TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. RFx Questions table (if not created in step 1)
CREATE TABLE IF NOT EXISTS ktq.rfx_questions (
    id BIGSERIAL PRIMARY KEY,
    rfx_id BIGINT NOT NULL REFERENCES ktq.rfx(id) ON DELETE CASCADE,
    question_number INTEGER NOT NULL DEFAULT 1,
    question_text TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    is_mandatory BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Vendor Responses table
CREATE TABLE IF NOT EXISTS ktq.vendor_responses (
    id BIGSERIAL PRIMARY KEY,
    rfx_id BIGINT REFERENCES ktq.rfx(id) ON DELETE SET NULL,
    vendor_id BIGINT REFERENCES ktq.vendors(id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 1,
    supersedes_response_id BIGINT REFERENCES ktq.vendor_responses(id) ON DELETE SET NULL,
    channel TEXT NOT NULL DEFAULT 'upload', -- email | upload | api | simulated
    subject TEXT,
    sender_name TEXT,
    sender_email TEXT,
    body_text TEXT,
    body_json JSONB,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'received',
    rfx_resolution JSONB NOT NULL DEFAULT '{}'::jsonb,
    vendor_resolution JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    is_current BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Check constraint on response status
ALTER TABLE ktq.vendor_responses DROP CONSTRAINT IF EXISTS vendor_responses_status_check;
ALTER TABLE ktq.vendor_responses ADD CONSTRAINT vendor_responses_status_check
    CHECK (status = ANY (ARRAY[
        'received',
        'preprocessing',
        'extracting',
        'resolving',
        'matching',
        'normalizing',
        'validating',
        'done',
        'needs_review',
        'failed'
    ]::text[]));

-- 4. Response Documents table
CREATE TABLE IF NOT EXISTS ktq.response_documents (
    id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL REFERENCES ktq.vendor_responses(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime TEXT,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    raw_bytes BYTEA,
    enhanced_bytes BYTEA,
    doc_role TEXT DEFAULT 'quotation',
    parsed_text TEXT,
    page_count INTEGER NOT NULL DEFAULT 1,
    quality_notes TEXT,
    status TEXT NOT NULL DEFAULT 'received',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure all columns exist if table was previously created with different columns
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'filename') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN filename TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'mime') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN mime TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'size_bytes') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN size_bytes BIGINT DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'sha256') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN sha256 TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'raw_bytes') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN raw_bytes BYTEA;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'enhanced_bytes') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN enhanced_bytes BYTEA;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'doc_role') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN doc_role TEXT DEFAULT 'quotation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'parsed_text') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN parsed_text TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'page_count') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN page_count INTEGER DEFAULT 1;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'quality_notes') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN quality_notes TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'status') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN status TEXT DEFAULT 'received';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'error') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN error TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'response_documents' AND column_name = 'created_at') THEN
        ALTER TABLE ktq.response_documents ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW();
    END IF;
    -- Drop legacy foreign key pointing to rfx_responses if present
    BEGIN
        ALTER TABLE ktq.response_documents DROP CONSTRAINT IF EXISTS response_documents_response_id_fkey;
        ALTER TABLE ktq.response_documents ADD CONSTRAINT response_documents_response_id_fkey 
            FOREIGN KEY (response_id) REFERENCES ktq.vendor_responses(id) ON DELETE CASCADE;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;
END $$;

-- 5. Response Items table
CREATE TABLE IF NOT EXISTS ktq.response_items (
    id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL REFERENCES ktq.vendor_responses(id) ON DELETE CASCADE,
    rfx_item_id BIGINT REFERENCES ktq.rfx_items(id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'MATCHED', -- MATCHED | EXTRA | ALTERNATE | NOT_QUOTED
    vendor_line_no TEXT,
    raw_description TEXT,
    raw_specs JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_qty NUMERIC,
    raw_price NUMERIC,
    raw_unit TEXT,
    raw_currency TEXT DEFAULT 'INR',
    tax_basis TEXT DEFAULT 'unknown', -- inclusive | exclusive | unknown
    discount JSONB NOT NULL DEFAULT '{}'::jsonb,
    moq NUMERIC,
    lead_time_days INTEGER,
    remarks TEXT,
    source_document_id BIGINT REFERENCES ktq.response_documents(id) ON DELETE SET NULL,
    source_snippet TEXT,
    source_location TEXT,
    page INTEGER,
    bbox JSONB NOT NULL DEFAULT '{}'::jsonb,
    extraction_confidence NUMERIC DEFAULT 1.0,
    extraction_reason TEXT,
    match_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
    match_confidence NUMERIC DEFAULT 1.0,
    match_reason TEXT,
    unit_factor NUMERIC NOT NULL DEFAULT 1.0,
    fx_rate NUMERIC NOT NULL DEFAULT 1.0,
    fx_rate_date DATE,
    normalized_price_inr NUMERIC,
    missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    state TEXT NOT NULL DEFAULT 'CONFIDENT', -- CONFIDENT | REVIEW | MISSING
    flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    why_unsure TEXT,
    how_to_resolve TEXT,
    buyer_override JSONB NOT NULL DEFAULT '{}'::jsonb,
    effective_price_inr NUMERIC,
    review_status TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected | corrected
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    is_current BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Item Crops (Visual Evidence)
CREATE TABLE IF NOT EXISTS ktq.item_crops (
    id BIGSERIAL PRIMARY KEY,
    item_id BIGINT NOT NULL REFERENCES ktq.response_items(id) ON DELETE CASCADE,
    document_id BIGINT REFERENCES ktq.response_documents(id) ON DELETE SET NULL,
    page INTEGER NOT NULL DEFAULT 1,
    bbox JSONB NOT NULL DEFAULT '{}'::jsonb,
    image_bytes BYTEA NOT NULL,
    mime TEXT NOT NULL DEFAULT 'image/png',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. Response Terms table
CREATE TABLE IF NOT EXISTS ktq.response_terms (
    id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL REFERENCES ktq.vendor_responses(id) ON DELETE CASCADE,
    term_type TEXT NOT NULL, -- freight | gst | payment_terms | delivery | warranty | discount | other
    value_text TEXT,
    value_num NUMERIC,
    condition_text TEXT,
    source_snippet TEXT,
    confidence NUMERIC DEFAULT 1.0,
    state TEXT DEFAULT 'CONFIDENT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 8. Response Answers table
CREATE TABLE IF NOT EXISTS ktq.response_answers (
    id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL REFERENCES ktq.vendor_responses(id) ON DELETE CASCADE,
    rfx_question_id BIGINT REFERENCES ktq.rfx_questions(id) ON DELETE SET NULL,
    raw_question TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    pass_fail TEXT NOT NULL DEFAULT 'unanswered', -- pass | fail | unanswered | needs_review
    reason TEXT,
    source_snippet TEXT,
    confidence NUMERIC DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9. Response Flags table
CREATE TABLE IF NOT EXISTS ktq.response_flags (
    id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL REFERENCES ktq.vendor_responses(id) ON DELETE CASCADE,
    item_id BIGINT REFERENCES ktq.response_items(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning', -- info | warning | critical
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved BOOLEAN NOT NULL DEFAULT false,
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ
);

-- 10. Pipeline Events table
CREATE TABLE IF NOT EXISTS ktq.pipeline_events (
    id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL REFERENCES ktq.vendor_responses(id) ON DELETE CASCADE,
    document_id BIGINT REFERENCES ktq.response_documents(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL, -- started | completed | failed | skipped
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms INTEGER NOT NULL DEFAULT 0,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- 11. LLM Calls table
CREATE TABLE IF NOT EXISTS ktq.llm_calls (
    id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL REFERENCES ktq.vendor_responses(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    validation_ok BOOLEAN NOT NULL DEFAULT true,
    retries INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 12. Reference Config Tables
CREATE TABLE IF NOT EXISTS ktq.unit_conversions (
    id BIGSERIAL PRIMARY KEY,
    from_unit TEXT NOT NULL,
    to_unit TEXT NOT NULL,
    multiplier NUMERIC NOT NULL,
    notes TEXT,
    CONSTRAINT unq_unit_conversion UNIQUE (from_unit, to_unit)
);

CREATE TABLE IF NOT EXISTS ktq.fx_rates (
    id BIGSERIAL PRIMARY KEY,
    from_currency TEXT NOT NULL,
    to_currency TEXT NOT NULL,
    rate NUMERIC NOT NULL,
    rate_date DATE NOT NULL DEFAULT CURRENT_DATE,
    source TEXT DEFAULT 'manual_config',
    CONSTRAINT unq_fx_rate UNIQUE (from_currency, to_currency, rate_date)
);

CREATE TABLE IF NOT EXISTS ktq.validation_thresholds (
    id BIGSERIAL PRIMARY KEY,
    rule_code TEXT NOT NULL UNIQUE,
    warning_threshold NUMERIC,
    critical_threshold NUMERIC,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    description TEXT
);

-- Seed basic conversion data
INSERT INTO ktq.unit_conversions (from_unit, to_unit, multiplier, notes)
VALUES 
    ('per 100', 'pcs', 0.01, 'Per 100 pieces to single piece'),
    ('per 1000', 'pcs', 0.001, 'Per 1000 pieces to single piece'),
    ('per 100 pcs', 'pcs', 0.01, 'Per 100 pieces to single piece'),
    ('per 1000 pcs', 'pcs', 0.001, 'Per 1000 pieces to single piece'),
    ('dozen', 'pcs', 0.0833333333, '12 pieces'),
    ('kg', 'kg', 1.0, 'Identity'),
    ('g', 'kg', 0.001, 'Grams to Kilograms'),
    ('gm', 'kg', 0.001, 'Grams to Kilograms'),
    ('grams', 'kg', 0.001, 'Grams to Kilograms'),
    ('sqm', 'sqm', 1.0, 'Identity'),
    ('sqft', 'sqm', 0.092903, 'Square feet to square meters'),
    ('roll', 'roll', 1.0, 'Identity'),
    ('pcs', 'pcs', 1.0, 'Identity'),
    ('box', 'pcs', 1.0, 'Single box to pieces')
ON CONFLICT (from_unit, to_unit) DO NOTHING;

INSERT INTO ktq.fx_rates (from_currency, to_currency, rate, rate_date, source)
VALUES
    ('INR', 'INR', 1.0, CURRENT_DATE, 'system'),
    ('USD', 'INR', 86.50, CURRENT_DATE, 'system'),
    ('EUR', 'INR', 93.00, CURRENT_DATE, 'system'),
    ('GBP', 'INR', 110.00, CURRENT_DATE, 'system'),
    ('AED', 'INR', 23.55, CURRENT_DATE, 'system'),
    ('SGD', 'INR', 64.20, CURRENT_DATE, 'system')
ON CONFLICT (from_currency, to_currency, rate_date) DO NOTHING;

-- 13. Read-Only SQL Views for Step 3 Hand-off

-- View 1: Current Active Response Items with Effective Price
CREATE OR REPLACE VIEW ktq.v_response_items_current AS
SELECT 
    ri.id AS response_item_id,
    ri.response_id,
    vr.rfx_id,
    r.title AS rfx_title,
    vr.vendor_id,
    COALESCE(v.name, vr.sender_name, 'Unknown Vendor') AS vendor_name,
    v.email AS vendor_email,
    ri.rfx_item_id,
    rxi.item_number AS rfx_item_number,
    rxi.description AS rfx_item_description,
    rxi.quantity AS rfx_quantity,
    rxi.unit AS rfx_unit,
    ri.kind,
    ri.vendor_line_no,
    ri.raw_description,
    ri.raw_specs,
    ri.raw_qty,
    ri.raw_price,
    ri.raw_unit,
    ri.raw_currency,
    ri.tax_basis,
    ri.discount,
    ri.moq,
    ri.lead_time_days,
    ri.unit_factor,
    ri.fx_rate,
    ri.normalized_price_inr,
    COALESCE((ri.buyer_override->>'corrected_price_inr')::numeric, ri.normalized_price_inr) AS effective_price_inr,
    ri.state,
    ri.flags,
    ri.why_unsure,
    ri.how_to_resolve,
    ri.review_status,
    ri.buyer_override,
    ri.is_current,
    ri.created_at
FROM ktq.response_items ri
JOIN ktq.vendor_responses vr ON ri.response_id = vr.id
LEFT JOIN ktq.vendors v ON vr.vendor_id = v.id
LEFT JOIN ktq.rfx r ON vr.rfx_id = r.id
LEFT JOIN ktq.rfx_items rxi ON ri.rfx_item_id = rxi.id
WHERE ri.is_current = TRUE AND vr.is_current = TRUE;

-- View 2: Vendor Coverage & Quotation Summary per RFx
CREATE OR REPLACE VIEW ktq.v_vendor_coverage AS
SELECT 
    vr.rfx_id,
    r.title AS rfx_title,
    vr.id AS response_id,
    vr.vendor_id,
    COALESCE(v.name, vr.sender_name, 'Unknown Vendor') AS vendor_name,
    vr.status AS response_status,
    (SELECT COUNT(*) FROM ktq.rfx_items WHERE rfx_id = vr.rfx_id) AS total_rfx_items,
    COUNT(ri.id) FILTER (WHERE ri.kind = 'MATCHED') AS matched_items_count,
    COUNT(ri.id) FILTER (WHERE ri.kind = 'EXTRA') AS extra_items_count,
    COUNT(ri.id) FILTER (WHERE ri.kind = 'ALTERNATE') AS alternate_items_count,
    COUNT(ri.id) FILTER (WHERE ri.kind = 'NOT_QUOTED') AS not_quoted_count,
    COUNT(ri.id) FILTER (WHERE ri.state = 'CONFIDENT') AS confident_items_count,
    COUNT(ri.id) FILTER (WHERE ri.state = 'REVIEW') AS review_items_count,
    COUNT(ri.id) FILTER (WHERE ri.state = 'MISSING') AS missing_items_count,
    ROUND(
        (COUNT(ri.id) FILTER (WHERE ri.kind IN ('MATCHED', 'ALTERNATE'))::numeric / 
        NULLIF((SELECT COUNT(*) FROM ktq.rfx_items WHERE rfx_id = vr.rfx_id), 0)) * 100, 
        2
    ) AS coverage_pct,
    AVG(COALESCE((ri.buyer_override->>'corrected_price_inr')::numeric, ri.normalized_price_inr)) FILTER (WHERE ri.kind = 'MATCHED') AS avg_normalized_price_inr,
    MIN(COALESCE((ri.buyer_override->>'corrected_price_inr')::numeric, ri.normalized_price_inr)) FILTER (WHERE ri.kind = 'MATCHED') AS min_normalized_price_inr,
    MAX(COALESCE((ri.buyer_override->>'corrected_price_inr')::numeric, ri.normalized_price_inr)) FILTER (WHERE ri.kind = 'MATCHED') AS max_normalized_price_inr,
    vr.received_at
FROM ktq.vendor_responses vr
LEFT JOIN ktq.vendors v ON vr.vendor_id = v.id
LEFT JOIN ktq.rfx r ON vr.rfx_id = r.id
LEFT JOIN ktq.response_items ri ON ri.response_id = vr.id AND ri.is_current = TRUE
WHERE vr.is_current = TRUE
GROUP BY vr.rfx_id, r.title, vr.id, vr.vendor_id, v.name, vr.sender_name, vr.status, vr.received_at;

-- View 3: Open / Unresolved Flags
CREATE OR REPLACE VIEW ktq.v_open_flags AS
SELECT 
    rf.id AS flag_id,
    rf.response_id,
    vr.rfx_id,
    COALESCE(v.name, vr.sender_name) AS vendor_name,
    rf.item_id,
    ri.vendor_line_no,
    ri.raw_description,
    rf.code,
    rf.severity,
    rf.message,
    rf.created_at
FROM ktq.response_flags rf
JOIN ktq.vendor_responses vr ON rf.response_id = vr.id
LEFT JOIN ktq.vendors v ON vr.vendor_id = v.id
LEFT JOIN ktq.response_items ri ON rf.item_id = ri.id
WHERE rf.resolved = FALSE;

-- View 4: RFx Line Item Comparison across Vendors
CREATE OR REPLACE VIEW ktq.v_rfx_comparison AS
SELECT 
    rxi.rfx_id,
    rxi.id AS rfx_item_id,
    rxi.item_number,
    rxi.description AS rfx_description,
    rxi.quantity AS rfx_quantity,
    rxi.unit AS rfx_unit,
    rxi.target_price AS rfx_target_price,
    vr.vendor_id,
    COALESCE(v.name, vr.sender_name) AS vendor_name,
    vr.id AS response_id,
    ri.id AS response_item_id,
    ri.kind,
    ri.raw_price,
    ri.raw_unit,
    ri.raw_currency,
    ri.unit_factor,
    ri.normalized_price_inr,
    COALESCE((ri.buyer_override->>'corrected_price_inr')::numeric, ri.normalized_price_inr) AS effective_price_inr,
    ri.lead_time_days,
    ri.tax_basis,
    ri.state,
    ri.review_status
FROM ktq.rfx_items rxi
LEFT JOIN ktq.response_items ri ON ri.rfx_item_id = rxi.id AND ri.is_current = TRUE
LEFT JOIN ktq.vendor_responses vr ON ri.response_id = vr.id AND vr.is_current = TRUE
LEFT JOIN ktq.vendors v ON vr.vendor_id = v.id
ORDER BY rxi.rfx_id, rxi.item_number, effective_price_inr ASC NULLS LAST;

-- 14. Compatibility Views for Legacy / Supplier Schemas
CREATE OR REPLACE VIEW ktq.suppliers AS 
SELECT 
    id, 
    name, 
    name AS contact_person, 
    email, 
    phone, 
    gstin AS gst_number, 
    address, 
    'active' AS status, 
    created_at, 
    updated_at 
FROM ktq.vendors;

CREATE OR REPLACE VIEW ktq.rfx_responses AS 
SELECT 
    id, 
    rfx_id, 
    vendor_id AS supplier_id, 
    id::text AS response_number, 
    status, 
    'INR' AS quoted_currency, 
    received_at AS submitted_at, 
    created_at, 
    updated_at 
FROM ktq.vendor_responses;

CREATE OR REPLACE VIEW ktq.rfx_response_items AS 
SELECT 
    id, 
    response_id, 
    rfx_item_id, 
    vendor_line_no, 
    raw_description AS description, 
    raw_qty AS quantity, 
    raw_unit AS unit, 
    raw_price AS unit_price, 
    normalized_price_inr AS total_price, 
    lead_time_days, 
    created_at 
FROM ktq.response_items;

