-- Migration 001: Packaging RFI Schema & Lifecycle Updates
-- Compatible with PostgreSQL and dynamically templated with DB_SCHEMA

CREATE SCHEMA IF NOT EXISTS ktq;

CREATE TABLE IF NOT EXISTS ktq.rfx (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Corrugated packaging',
    scope TEXT,
    currency TEXT NOT NULL DEFAULT 'INR',
    payment_terms TEXT DEFAULT 'Net 30',
    delivery_terms TEXT DEFAULT 'Delivered to warehouse dock',
    validity_days INTEGER DEFAULT 30,
    response_deadline DATE,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT DEFAULT 'text_intake',
    released_at TIMESTAMPTZ,
    triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure sequence exists for rfx if id was created as bigint without sequence
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_sequences WHERE schemaname = 'ktq' AND sequencename = 'rfx_id_seq') THEN
        BEGIN
            CREATE SEQUENCE IF NOT EXISTS ktq.rfx_id_seq;
            ALTER TABLE ktq.rfx ALTER COLUMN id SET DEFAULT nextval('ktq.rfx_id_seq');
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
    END IF;
END $$;

-- Add missing columns safely if table already existed
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'rfx' AND column_name = 'source') THEN
        ALTER TABLE ktq.rfx ADD COLUMN source TEXT DEFAULT 'text_intake';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'rfx' AND column_name = 'triggered_at') THEN
        ALTER TABLE ktq.rfx ADD COLUMN triggered_at TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'rfx' AND column_name = 'updated_at') THEN
        ALTER TABLE ktq.rfx ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
    END IF;
END $$;

-- Update status check constraint to support full RFI lifecycle
ALTER TABLE ktq.rfx DROP CONSTRAINT IF EXISTS rfx_status_check;
ALTER TABLE ktq.rfx ADD CONSTRAINT rfx_status_check 
    CHECK (status = ANY (ARRAY[
        'draft',
        'ready',
        'triggered',
        'responses_pending',
        'completed',
        'released',
        'closed'
    ]::text[]));

-- Ensure rfx_items table exists
CREATE TABLE IF NOT EXISTS ktq.rfx_items (
    id BIGSERIAL PRIMARY KEY,
    rfx_id BIGINT NOT NULL REFERENCES ktq.rfx(id) ON DELETE CASCADE,
    item_number INTEGER NOT NULL,
    item_code TEXT,
    description TEXT NOT NULL,
    quantity NUMERIC NOT NULL DEFAULT 1,
    unit TEXT DEFAULT 'pcs',
    material TEXT,
    dimensions JSONB DEFAULT '{}'::jsonb,
    specifications TEXT,
    target_price NUMERIC,
    currency TEXT NOT NULL DEFAULT 'INR',
    required_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure sequence exists for rfx_items if id was created as bigint without sequence
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_sequences WHERE schemaname = 'ktq' AND sequencename = 'rfx_items_id_seq') THEN
        BEGIN
            CREATE SEQUENCE IF NOT EXISTS ktq.rfx_items_id_seq;
            ALTER TABLE ktq.rfx_items ALTER COLUMN id SET DEFAULT nextval('ktq.rfx_items_id_seq');
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
    END IF;
END $$;

-- Add dimensions and material columns to rfx_items if missing
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'rfx_items' AND column_name = 'dimensions') THEN
        ALTER TABLE ktq.rfx_items ADD COLUMN dimensions JSONB DEFAULT '{}'::jsonb;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ktq' AND table_name = 'rfx_items' AND column_name = 'material') THEN
        ALTER TABLE ktq.rfx_items ADD COLUMN material TEXT;
    END IF;
END $$;
