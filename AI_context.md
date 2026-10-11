# AI Context & Project Source of Truth: Packaging RFx & Procurement Analyst Platform

## 1. Executive System Definition

The platform is an enterprise AI procurement assistant and decision-support system built with **FastAPI**, **PydanticAI**, and **PostgreSQL**, connected to custom Vue 3 / Vuetify client dashboards (`chatbot/index.html` and `chatbot/analyst.html`).

### Key Capabilities
- **End-to-End Packaging RFI Intake**: Multi-modal requirement gathering via natural language chat, multi-column enterprise tabular BOQ parsing (TSV/Excel pastes with 30+ items per turn), commercial terms auto-separation, duplicate detection, and dynamic line-item operations (batch removals, updates, contiguous `1..N` re-indexing).
- **Multi-Modal Vendor Quotation Ingestion & OCR**: Handles supplier rate cards and quotations in any format (Excel, PDF scans with rasterization, Word, EML emails, images), stores raw bytes byte-for-byte with SHA-256 idempotency scoped to `rfx_id`, resolves quotes via a 6-signal scored engine, extracts volumetric discount tables/MOQs, normalizes units and currencies to INR, and generates visual 10%-padded PNG crops.
- **Decision Analyst & Semantic Intelligence Layer**: Natural language decision-support Q&A powered by the Wren MDL semantic layer and normalized few-shot retrieval, guarded read-only SQL with `sqlglot` AST allowlist enforcement, token-optimized artifact caching (0 tokens on repeated queries), and 5 ready-made standard SQL comparison widgets.
- **Deterministic Award Optimization**: Multi-criteria mathematical sourcing solver (L1 cheapest per line, single vendor, multi-vendor share cap split), Minimum Order Quantity (MOQ) penalty constraints (`shortfall_penalty`, `quantity_bump`, `exclude_below_moq`), and knockout quality questionnaire gates. Operates strictly within a decision-support simulation boundary (no binding contractual awards).
- **Host-Agnostic Cloud & Serverless Deployment**: Decoupled origin-relative endpoints (`/api/...`), automatic migration of legacy `localStorage` endpoint caches, Vercel Serverless runtime support, and production runner (`scripts/run_server.sh`) binding to `0.0.0.0`.

---

## 2. Core Functional Pillars

### Pillar 1: Conversational RFI Intake & Tabular BOQ Parser
- **Session Continuity**: `ChatSessionState` persistently tracks `active_rfi_id`. Incoming user requirements augment the active draft rather than creating fragmented new RFIs.
- **Enterprise Tabular BOQ Intake**: Directly parses multi-column TSV, pipe, or column-aligned tables pasted into chat. Captures SKU codes, descriptions, categories, quantities, standard UOMs, baseline prices in INR, and 2D/3D dimensions into typed `PackagingRequirement` items.
- **Commercial Terms Normalization**: Separates non-product commercial sentences (payment terms, delivery terms, quote validity, freight instructions) and RFP solicitation preambles from line items and stores them in RFI metadata.
- **Dynamic Line Item Operations**: Batch deletions (`remove 33 to 39`), multi-item lists (`delete 2, 4, 6`), relative removals (`remove last item`), and attribute updates (`update item 1 quantity to 1500`) with automatic contiguous `1..N` re-indexing via PostgreSQL `ROW_NUMBER()`.

### Pillar 2: Vendor Quotation Intake, OCR & Volumetric Extraction
- **Asynchronous Ingestion (202 Accepted)**: Raw files are stored byte-for-byte before processing. Duplicate detection (`is_duplicate`) is strictly evaluated within the scope of the target RFx (`rfx_id`), allowing identical quotation files to be uploaded across distinct RFxs without collisions.
- **6-Signal Scored Resolution Engine**: Weighted signals determine target RFx and vendor: explicit `rfx_id`/`rfx_ref`, subject pattern regex, reply-thread headers, line item similarity, open RFx counts, and recency fallback. Automatically creates vendor entity records when missing.
- **Volumetric Rate Tables & OCR Scans**: Captures multi-tiered quantity break tables (e.g., `100-500 pcs: ₹45, 501-2000 pcs: ₹40, 2001+ pcs: ₹36 | MOQ: 100`). Records lower bounds as line MOQ, populates structured discount metadata `{'type': 'volumetric', 'tiers': [...]}` into `response_items.discount`, and extracts each quantity tier as an explicit line item.
- **Pure Python Normalization & Validation**: Rule-based conversions of foreign currencies to INR, packaging unit standardization, detection of pricing traps (such as per-100 or per-1000 rates), and classification into `CONFIDENT`, `REVIEW`, or `MISSING` validation states with plain-language explanations.

### Pillar 3: Decision Analyst, Semantic Text-to-SQL & Caching
- **Semantic Modeling & Few-Shot Retrieval**: Uses Wren MDL semantic models (`config/wren_semantic_model.yaml`, `config/wren_mdl.json`) and 24+ few-shot SQL examples (`config/wren_examples.yaml`) normalized for stop-words and punctuation. Examples include complex multi-table joins cross-referencing questionnaire answers, terms, open flags, and line item prices.
- **Dual-Mode Narrative Synthesis**:
  - *LLM Mode*: Uses `config/prompts/analyst_system_v1.txt` to synthesize an executive summary with direct answers, win distributions, and nuanced trade-off analyses (quantifying cost premium delta between split scenarios and L1, supplier concentration risk reduction, and operational overhead). Leverages LLMs wherever available to generate high-value, nuanced procurement insights while strictly masking internal database primary keys.
  - *Deterministic Fallback*: Analyzes query rows programmatically to construct structured markdown for L1 allocations, target price savings, vendor coverage, and open review flags.
- **Human-Readable Item Presentation**: All client-facing tables, LLM prompt contexts, and narrative breakdowns strictly reference line items by their human-readable **Item #** and **Item Description / Name** (e.g. `Item #1: 3-Ply Corrugated Box`). Technical database keys (`rfx_item_id`, `vendor_id`, `response_id`) are stripped from presentation layers.
- **Ground Truth Traceability Post-Check**: All numbers cited in generated text are verified against database query rows; ungrounded figures automatically downgrade confidence to MEDIUM or LOW.
- **0-Token Artifact Caching & Validation**: Automatically persists generated tables and charts per `rfx_id` in `AnalystRepository`. Queries for identical RFx analytics return cached artifacts with 0 tokens consumed. Employs active validation (`_is_valid_cached_response`) to discard and purge stale or legacy placeholder responses.
- **5 Standard SQL Benchmark Widgets**:
  1. *Overall Basket KPI Banner* (Target Spend, Optimal Spend, Savings Amount/%, Full vs Partial Quote counts).
  2. *Vendor Response Coverage Breakdown* (100% full quote coverage vs partial quotes, spend per vendor).
  3. *L1 Best Price Summary & Savings* (Lowest quoted prices vs targets per item).
  4. *Price Spread & Bidder Variance* (Min, Max, Absolute Spread, Spread % per line item).
  5. *Vendor Win Leaderboard* (Items won L1 and spend share).

### Pillar 4: Award Optimization Engine & MOQ Constraints
- **Deterministic Solver (`backend/app/analyst/optimizer.py`)**: Computes hypothetical allocation scenarios:
  - `cheapest_per_line`: Unconstrained lowest cost per line item.
  - `single_vendor`: Lowest total spend from a single supplier capable of quoting all items.
  - `multi_vendor_split`: Volume-capped split (e.g. max 60% share to any single vendor).
- **Minimum Order Quantity (MOQ) Penalty Constraints**:
  - `none`: Standard allocation without MOQ enforcement.
  - `shortfall_penalty`: Adds a shortfall penalty `(MOQ - Qty) * Unit Price * Penalty Rate` to line spend.
  - `quantity_bump`: Bumps order quantity to vendor MOQ (`MOQ * Unit Price`), reporting surplus cost.
  - `exclude_below_moq`: Disqualifies suppliers whose MOQ exceeds required volume for that line.
- **Knockout Quality Gate**: Enforces mandatory pass requirements on technical and compliance questionnaire answers.
- **Decision-Support Scope**: Solvers provide strategy simulations only; the system does not execute binding contractual awards or alter RFx lifecycle status.

### Pillar 5: SQL AST Security Parser (`backend/app/analyst/tools/sql_runner.py`)
- **AST Allowlist Enforcement (`sqlglot`)**: Parses queries into an AST using PostgreSQL dialect and recursively traverses `exp.Table` nodes across main queries, CTE definitions, subqueries in `WHERE`/`FROM`/`JOIN`, and scalar projections.
- **Internal CTE Isolation**: Isolates internal CTE aliases defined in the query so they are not mistaken for external unallowed tables.
- **Strict Table Allowlist**: Restricts table and view access strictly to `ALLOWED_TABLES_AND_VIEWS` (e.g., `ktq.v_rfx_comparison`, `ktq.v_vendor_coverage`, `ktq.v_open_flags`, `ktq.rfx`, etc.), blocking unauthorized catalog or table access.
- **Runtime Guardrails**: Enforces `statement_timeout = 5000ms`, single-statement execution, read-only keywords, and parameter limits (`LIMIT <= 500`).

---

## 3. Database Schema Reference

Dynamic schema (`DB_SCHEMA`, default `ktq`) managed via parameterized PostgreSQL connection:

### Tables
- `ktq.rfx`: Master RFx record (id, title, category, scope, currency, payment_terms, delivery_terms, validity_days, response_deadline, status, triggered_at).
- `ktq.rfx_items`: Line item specifications (item_number, description, quantity, unit, material, dimensions, specifications, target_price).
- `ktq.rfx_questions`: Technical and compliance questionnaire definitions and knockout flags.
- `ktq.vendors`: Supplier master data (name, email, phone, gstin, address).
- `ktq.vendor_responses`: Quotation submissions (channel, sender details, status, is_current versioning).
- `ktq.response_documents`: Raw file bytes, SHA-256 hash, mime type, size, parsed OCR text, page count.
- `ktq.response_items`: Extracted line items, matched RFx item, raw/normalized prices, moq, unit factor, flags, crop coordinates.
- `ktq.response_terms`: Quoted commercial conditions (payment_terms, freight, validity, discounts).
- `ktq.response_answers`: Supplier questionnaire answers and knockout pass/fail status.
- `ktq.analyst_traces`: Q&A interaction telemetry, tools called, SQL executed, latencies, response JSON.
- `ktq.award_scenarios`: Stored allocation scenarios, constraints, and buyer finalization records.
- `ktq.analyst_audit_log`: Immutable audit log recording scenario finalization and accepted REVIEW flags.

### Analytical SQL Views
- `ktq.v_rfx_comparison`: Cross-vendor side-by-side comparison matrix per RFx item with effective INR price, ranking, and flags.
- `ktq.v_vendor_coverage`: Quotation coverage summary per vendor response (total items, quoted count, confident quotes, spend totals).
- `ktq.v_open_flags`: Unresolved validation flags with severity, description, and remediation guidance.
- `ktq.v_response_items_current`: Active line items with document metadata, normalized INR prices, and evidence crop URLs.

---

## 4. Key Codebase Index

| File / Directory | Role & Responsibilities |
|---|---|
| `backend/app/main.py` | FastAPI application factory, lifespan agent initialization, static mount, CORS. |
| `api/index.py` | Vercel Serverless ASGI entrypoint. |
| `backend/app/agents/master.py` | Master conversational orchestrator, session tracker, intent router, line-item command handler. |
| `backend/app/intake/` | Intake parsers (`agent.py`, `excel_parser.py`, `validation.py`). |
| `backend/app/vendor/` | Quotation extraction pipeline (`service.py`, `preprocess.py`, `resolve.py`, `extract.py`, `match.py`, `normalize.py`, `validate.py`, `evidence.py`, `repository.py`, `prompts.py`). |
| `backend/app/analyst/` | Decision intelligence engine (`orchestrator.py`, `semantic.py`, `optimizer.py`, `repository.py`, `models.py`, `charts.py`, `exports.py`, `tools/`). |
| `backend/app/analyst/tools/sql_runner.py` | Read-only SQL execution with `sqlglot` AST security parser and table allowlists. |
| `chatbot/index.html` | Unified chat UI workbench with progress loaders and dynamic API resolution. |
| `chatbot/analyst.html` | 4-tab Decision Analyst dashboard (RFI Details, Comparison Matrix, Vendor Comparison, AI Chat). |
| `config/prompts/analyst_system_v1.txt` | Decision analyst system prompt enforcing comparability, zero math hallucinations, and multi-vendor trade-offs. |
| `config/wren_examples.yaml` | 24+ few-shot question-to-SQL pairs including complex multi-table joins. |
| `config/wren_semantic_model.yaml` | Wren MDL semantic model definitions. |
| `scripts/run_server.sh` | Production cloud startup script binding to `0.0.0.0` with proxy header forwarding. |

---

## 5. Verification & Test Suite Status

The platform maintains 100% passing test suites across all core subsystems:
- **Pytest Regression Suite**: **86 passed**, 14 skipped (live PostgreSQL tests skipped gracefully when offline) with trace logs in `artifacts/logs/test_fine_tuning_and_consolidation.log`.
- **Decision Analyst Evaluation Benchmark (`scripts/eval_analyst.py`)**: 33-question benchmark suite passing 100% (33/33).
- **Vendor Extraction Evaluation (`scripts/eval_extraction.py`)**: 100% extraction accuracy on ground truth quotation sets.
