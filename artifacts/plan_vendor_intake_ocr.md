# Implementation Plan: Step 2 - Vendor Input Intake, OCR/Extraction, RFx Resolution, Normalization, Evidence Cropping & REST APIs

## 1. Objectives & Scope
- Accept vendor quotations in ANY format: `.xlsx`, `.csv`, `.pdf`, `.docx`, `.doc`, `.txt`, `.eml`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.heic`, scanned images, email text, JSON payloads.
- Multi-document combined submission under one response ID.
- Real LLM extraction with provider-agnostic models via PydanticAI / factory. No hardcoding or special-casing sample vendors.
- Scored 6-tier RFx and vendor resolution with confidence and explainability.
- Strict Pydantic JSON extraction with multi-document conflict reconciliation.
- Line item matching to `rfx_items`: `MATCHED`, `EXTRA`, `ALTERNATE`, `NOT_QUOTED`.
- Pure Python deterministic normalization & validations: unit conversions, fx rates, per-100 traps, outlier detection.
- Visual evidence cropping around bounding boxes for visual auditability.
- Read-only PostgreSQL views (`v_response_items_current`, `v_vendor_coverage`, `v_open_flags`, `v_rfx_comparison`) for downstream Step 3 analytics.
- Reprocess and buyer correction (`PATCH /api/response-items/{id}`) with audit history.
- Realistic sample generator (5 formats), evaluation suite, curl demo scripts, and documentation updates.

---

## 2. Phased Checkpoints

### CP1: Schema, Database Migration & Raw Storage Flow
- Create migration `migrations/002_vendor_intake.sql` establishing dynamic schema tables:
  - `vendors`
  - `vendor_responses`
  - `response_documents`
  - `response_items`
  - `item_crops`
  - `response_terms`
  - `response_answers`
  - `response_flags`
  - `pipeline_events`
  - `llm_calls`
  - Configuration tables: `unit_conversions`, `fx_rates`, `validation_thresholds`
- Update `backend/app/db/migrate.py` and repository models.
- Build raw ingestion service `backend/app/vendor/ingest.py`:
  - Immediate byte storage into `response_documents` and `vendor_responses`.
  - SHA256 idempotency check and superseding version control.
  - Return `202 Accepted` with `{response_id, status, rfx_resolution_preview}`.
  - Background task state machine trigger.

### CP2: Multi-format Document Preprocessing & Image Enhancement
- Build `backend/app/vendor/preprocess.py`:
  - Excel/CSV: `openpyxl`/`csv` coordinate-based sheet extraction (`Sheet1!A1`), merged cell handling.
  - PDF: text extraction (`pypdf`); rasterization for scanned pages.
  - Word: `python-docx` / XML parser for tables and paragraphs.
  - Email: `.eml`/MIME header & body extractor.
  - Image: auto-orient, contrast enhancement, grayscale/thresholding.
  - Per-document graceful failure handling.

### CP3: Scored RFx & Vendor Resolution Engine
- Build `backend/app/vendor/resolve.py`:
  - 6 signals evaluated with confidence and reasoning:
    1. Explicit `rfx_ref`
    2. Regex/LLM reference in subject/body/docs
    3. Reply-thread hints
    4. Content similarity against open `rfx_items`
    5. Sender known vendor on single open RFx
    6. Fallback recent RFx (with LOW confidence flag)
  - Ambiguity detection (score delta < threshold -> `AMBIGUOUS`, status `needs_review`).
  - Vendor resolution & auto-creation/flagging.

### CP4: Structured Extraction, Reconciliation & RFx Item Matching
- Build `backend/app/vendor/extract.py` and `backend/app/vendor/prompts.py`:
  - Pydantic models for extracted lines, terms, answers, doc quality.
  - Vision/text LLM extraction per document.
  - Cross-document reconciliation (e.g. email vs PDF conflicts).
- Build `backend/app/vendor/match.py`:
  - Match to `rfx_items`: `MATCHED`, `EXTRA`, `ALTERNATE`, `NOT_QUOTED`.
  - Many-to-one / one-to-many relationship tracking.

### CP5: Normalization, Validation, Visual Evidence & State Computation
- Build `backend/app/vendor/normalize.py`:
  - Unit conversions (e.g., per 100, kg/g, rolls, sqm).
  - Currency conversion to INR with rate date.
- Build `backend/app/vendor/validate.py`:
  - All validation rules (missing unit, price outlier, validity expired, trap detection).
  - State computation: `CONFIDENT`, `REVIEW`, `MISSING` with `why_unsure` and `how_to_resolve`.
  - Response-level summary generation.
- Build `backend/app/vendor/evidence.py`:
  - Crop region around bounding box with padding, save in `item_crops`.

### CP6: Endpoints, Read-Only Views, Reprocessing & Corrections
- Build `backend/app/api/vendor.py`:
  - `POST /api/vendor-responses`
  - `GET /api/vendor-responses/{id}`
  - `GET /api/vendor-responses/{id}/items`
  - `GET /api/vendor-responses/{id}/documents/{doc_id}/raw`
  - `GET /api/response-items/{item_id}/crop`
  - `GET /api/rfx/{rfx_id}/responses`
  - `POST /api/vendor-responses/{id}/reprocess`
  - `PATCH /api/response-items/{item_id}`
- Define views in SQL: `v_response_items_current`, `v_vendor_coverage`, `v_open_flags`, `v_rfx_comparison`.
- Register router in `backend/app/main.py`.

### CP7: Sample Generator, Evaluation Suite & Verification
- `scripts/generate_sample_responses.py` (5 realistic shapes + ground truth).
- `scripts/eval_extraction.py` (accuracy, review flags, zero silent errors).
- `scripts/demo_submit.sh` (curl commands).
- Pytest suite covering all services.
- Update `README.md`, `architecture.md`, `AI_context.md`.
