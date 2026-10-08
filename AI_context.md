# AI Context & Project Source of Truth

## Step 1: Packaging RFI Workflow & Intake System
- Configurable multi-agent chatbot system extended with end-to-end Packaging RFI workflow:
  - **Conversational RFI Chat Workflow (`POST /api/chat`)**:
    - **Welcome Card Initiation**: UI starter card updated to "Create an RFI".
    - **Chat Loader**: Added visual response loader bubble in chat stream and input box loading state whenever an outcome is expected (`generating == true`).
    - **Single Active RFI Continuity**: Sessions now persistently track and operate on a single active draft RFI (`ChatSessionState.active_rfi_id`). Subsequent user inputs add items to the active draft rather than creating fragmented new RFIs.
    - **Add & Remove Items Dynamically**: Users can add items incrementally or remove specific items by index/name (e.g. "remove item 2", "remove brown tape") with automatic line-item re-indexing.
    - **Duplicate Detection & User Confirmation**: When an incoming item resembles an existing line item in the active draft, the agent flags the duplicate and prompts the user for explicit confirmation before adding or updating.
    - **Advanced Natural Language Packaging Extraction**: Refined extraction for compound and domain-specific clauses, including cube boxes (`50inch cube boxes 300` -> 300 boxes of 50x50x50 inch), stretch film length specs (`5m stretch films 1000 pcs` -> 1000 pcs of 5m stretch film), weight units (`50kg` bubble wrap), and robust item boundary segmentation.
    - **Strict Guardrails**: When in RFI creation workflow, only solutions or progress for creating the RFI are provided. Inquiries about existing RFI status (e.g. "What is the status of RFX-101?", "Check vendor response") or external queries (vendor directories, external info) are not encouraged and politely redirected back to completing the RFI creation.
  - **Text Intake** (`POST /api/rfi/intake`): Natural language parsing of packaging requirements into structured `PackagingRequirement` items.
  - **Excel Intake** (`POST /api/rfi/intake/excel`): Openpyxl spreadsheet parser handling header variations, row-level validation, and canonical item extraction.
  - **PydanticAI Packaging Agent** (`backend/app/intake/agent.py`): Extracts typed requirements, dimensions, plies, material, required date, target price, and flags missing/ambiguous fields without hallucination. Configured as `rfi_parser` in `config/agents.yaml`.
  - **PostgreSQL Source of Truth** (`backend/app/db/`): Uses dynamic `DB_SCHEMA` (default: `ktq`). Parameterized repository (`RFIRepository`) manages `rfx`, `rfx_items`, and `rfx_activity` tables with atomic transactions, item add/remove methods, and memory fallback.
  - **Database Health Check** (`GET /health/db` & `GET /api/db/health`): Validates connection, verifies dynamic schema existence, and executes test query without credential leakage.
  - **System Health Check** (`GET /health` & `GET /api/health`): Validates running backend and registered agents.
  - **RFI Lifecycle APIs** (`backend/app/api/rfi.py`):
    - `POST /api/rfi`: Direct RFI creation.
    - `GET /api/rfi/{id}`: Retrieval of RFI and line items.
    - `PATCH /api/rfi/{id}`: Updating editable fields.
    - `POST /api/rfi/{id}/trigger`: Transitioning status to `TRIGGERED` and timestamp recording.
  - **Strict LLM Model Creation (`backend/app/providers/factory.py`)**: `create_model()` enforces strict provider credential validation (`fallback_to_mock=False` by default), raising an explicit `ValueError` when API keys are omitted. Explicit mock models (`provider="test"` or `fallback_to_mock=True`) remain supported for unit testing.
- Full pytest test suite passing 13 tests across vendor normalization, validation, resolution, preprocessing, and APIs with traceable logs in `artifacts/logs/test_full.log`.
- Automated evaluation (`scripts/eval_extraction.py`) passing 100% (8/8 ground truth items correct, 0 silent errors, crops generated for evidence).

## Step 2: Vendor Quotation Intake, Extraction & Validation System
- **Database Architecture (`migrations/002_vendor_intake.sql`)**:
  - Raw document persistence: `vendor_responses`, `response_documents` (stores raw bytea, sha256, mime, size).
  - Normalized extracted data: `vendors`, `response_items`, `item_crops`, `response_terms`, `response_answers`, `response_flags`.
  - Traceability: `pipeline_events`, `llm_calls`, `unit_conversions`, `fx_rates`, `validation_thresholds`.
  - Pre-built SQL views for Step 3: `v_response_items_current`, `v_vendor_coverage`, `v_open_flags`, `v_rfx_comparison`.
- **Pipeline Architecture & Components (`backend/app/vendor/`)**:
  - `models.py`: Strict Pydantic domain models for documents, extractions, line items, flags, and REST APIs.
  - `repository.py`: Parameterized PostgreSQL repository managing raw byte persistence, SHA-256 idempotency, version superseding, audit logs, and SQL views.
  - `preprocess.py`: Multi-format preprocessing: Excel/CSV with `[Sheet!Cell]` coordinates, PDF text + rasterization, Word paragraphs/tables, Email headers/body/attachments, and image auto-orient/enhancement.
  - `resolve.py`: 6-signal scored resolution engine (explicit ref, pattern in text, email thread, line item similarity, vendor open RFx count, fallback) with ambiguity detection and vendor entity auto-creation from extracted quotation/signature details.
  - `prompts.py` & `extract.py`: Provider-agnostic LLM extraction with strict PydanticAI validation, single retry with error feedback, and deterministic extractor supporting inline `body_text`, Indian Rupee glyphs (`₹`), multi-tier price clauses, and email signatures (contact person, company, phone, email, location).
  - `match.py`: Semantic line-item matching against `rfx_items` (`MATCHED`, `EXTRA`, `ALTERNATE`, `NOT_QUOTED`).
  - `normalize.py`: Pure Python deterministic unit conversions (e.g. `per 100` pricing traps) and foreign currency conversion to INR.
  - `validate.py`: Pure Python rule-based validation engine generating structured flags (`CONFIDENT`, `REVIEW`, `MISSING`), computing `why_unsure` and `how_to_resolve` for buyer review.
  - `evidence.py`: Visual bounding box cropping with 10% padding.
  - `service.py`: Pipeline coordinator executing state machine asynchronously.
- **REST Endpoints (`backend/app/api/vendor.py`)**:
  - `POST /api/vendor-responses` (202 Accepted, raw input persisted byte-for-byte immediately)
  - `GET /api/vendor-responses/{id}` (Status, progress, RFx/vendor resolution reasoning)
  - `GET /api/vendor-responses/{id}/items` (Extracted line items, flags, evidence)
  - `GET /api/vendor-responses/{id}/documents/{doc_id}/raw` (Original byte stream)
  - `GET /api/response-items/{item_id}/crop` (Visual evidence PNG)
  - `GET /api/rfx/{rfx_id}/responses` (Coverage summary and item comparisons)
  - `POST /api/vendor-responses/{id}/reprocess` (Re-run pipeline as new run)
  - `PATCH /api/response-items/{item_id}` (Buyer corrections with before/after audit trail)

## Step 3: Natural Language Analysis, Decision Support & Award Optimization
- **Database Architecture (`migrations/003_analyst.sql`)**:
  - `ktq.analyst_traces`: Detailed telemetry of each analyst interaction (question, tools called, SQL executed, path used, latencies, tokens, confidence, full JSON response).
  - `ktq.analyst_feedback`: User ratings, comments, corrected SQL, and corrected answers linked to traces.
  - `ktq.award_scenarios`: Stored allocation scenarios with constraints, overrides, and finalization status.
  - `ktq.analyst_audit_log`: Immutable audit trail recording finalized award scenarios and accepted REVIEW flags.
- **Decision Engine Components (`backend/app/analyst/`)**:
  - `models.py`: Strict Pydantic domain models for JSON response contract, tables, Chart.js specs, exports, caveats, `how_i_got_this`, feedback, award constraints, line allocations, and trust metrics.
  - `repository.py`: Parameterized PostgreSQL repository managing traces, session histories, feedback, and scenario lifecycles.
  - `semantic.py`: Semantic Text-to-SQL layer supporting Wren MDL models (`config/wren_semantic_model.yaml`, `config/wren_mdl.json`), few-shot retrieval (`config/wren_examples.yaml`), and single-retry self-correction loop.
  - `orchestrator.py`: Master Decision Analyst Orchestrator coordinating tools, comparability guardrails, state disclosure, and number traceability post-check.
  - `optimizer.py`: Deterministic Python solver for multi-criteria sourcing (cheapest per line, single vendor, max share cap % split, knockout quality filtering, what-if price/freight overrides).
  - `charts.py`: Standardized Chart.js JSON specification generator.
  - `exports.py`: Multi-tab Excel workbook generator (`.xlsx`) with mandatory *Assumptions & Caveats* sheet.
  - `tools/`: Modular tools (`sql_runner.py`, `trust.py`, `comparison.py`, `assumptions.py`).
- **REST Endpoints (`backend/app/api/analyst.py`)**:
  - `POST /api/analyst/ask`
  - `GET /api/analyst/sessions/{id}`
  - `POST /api/analyst/feedback`
  - `GET /api/rfx/{id}/comparison`
  - `GET /api/rfx/{id}/trust`
  - `POST /api/rfx/{id}/award/scenarios`
  - `GET /api/rfx/{id}/award/scenarios/{sid}`
  - `POST /api/rfx/{id}/award/scenarios/{sid}/finalize`
  - `GET /api/exports/{id}`
- **UI & Visualization (`chatbot/analyst.html`)**:
  - Single-page 4-tab dashboard: (1) Comparison Grid with evidence modals, (2) Trust & Risk Audit cards, (3) Analyst Chat with Chart.js charts and collapsible "How I Got This" drawer, (4) Award Scenarios with finalize sign-off flow.
- **Evaluation & Feedback Sync**:
  - `scripts/eval_analyst.py`: 33-question benchmark suite passing 100% (33/33) with property checks and logs in `artifacts/logs/eval_analyst.log`.
  - `scripts/export_feedback_to_examples.py`: Exporter syncing rated user corrections into `config/wren_examples.yaml`.
  - Full pytest regression suite passing 100% (69/69) with logs in `artifacts/logs/test_full_suite.log`.

## Key Files
- `backend/app/api/analyst.py`: FastAPI routes for decision analyst chat, grid, trust, and scenarios.
- `backend/app/analyst/`: Core decision engine modules (`orchestrator.py`, `semantic.py`, `optimizer.py`, `repository.py`, `models.py`, `charts.py`, `exports.py`, `tools/`).
- `chatbot/analyst.html`: 4-tab decision support UI workbench.
- `config/wren_semantic_model.yaml` & `config/wren_mdl.json`: Semantic layer definitions.
- `config/wren_examples.yaml`: 20+ question-to-SQL example pairs.
- `config/eval_questions.yaml`: 33-question benchmark suite.
- `migrations/003_analyst.sql`: Analyst telemetry, feedback, and scenario persistence schema.
- `scripts/eval_analyst.py`: Automated benchmark evaluation runner.
- `scripts/export_feedback_to_examples.py`: Feedback-to-examples sync tool.
- `artifacts/plan_analyst.md`: Step 3 master implementation plan.
- `artifacts/logs/eval_analyst.log`: 33-question benchmark run trace.
- `artifacts/logs/test_full_suite.log`: 69-test full regression execution trace.
- `artifacts/logs/test_cleanup_pass.log`: Test execution log verifying 69 tests across offline/mock and database-dependent components with graceful pytest skipping.

## Codebase Cleanup & Baseline Stabilization
- **Test Harness Standardization**: Created `tests/conftest.py` with dynamic `requires_db` marker to automatically and gracefully skip tests requiring live network/DB connections during offline runs.
- **Python Modernization & Cleanliness**:
  - Replaced deprecated `datetime.utcnow()` with `datetime.now(timezone.utc)` in `backend/app/vendor/service.py`.
  - Removed unused imports across `semantic.py`, `sql_runner.py`, `trust.py`, `optimizer.py`, and `analyst/repository.py`.
  - Standardized parameterized logging across analyst telemetry and semantic execution layers.
- **Documentation Parity**: Synchronized section numbering in `architecture.md`, completed the semantic decision intelligence layer overview, and cleaned Markdown syntax across `README.md` and `AI_context.md`.

## Step 4: Decision Analyst & Vendor Intelligence Unification
- **Workflow & Skill Consolidation**:
  - Combined disparate "Check Vendor Response" and "Analyst Chat" into a unified **AI Procurement Analyst & Vendor Responses** hub.
  - Eliminated external make.com webhook intercept (`isVendorFlow` / `vendorWebhookUrl`) returning raw `"Accepted"`; routed all responses directly to unified backend endpoints.
  - Simplified landing page to 2 clear primary workflow cards: (1) *Create an RFI* and (2) *AI Procurement Analyst & Vendor Responses*.
- **Token Optimization & Artifact Caching**:
  - Persisted and cached all generated graphs, charts, and table reports per `rfx_id` in `AnalystRepository`.
  - Implemented automatic token optimization in `DecisionAnalystOrchestrator.ask()`: queries for identical RFX queries return cached responses with 0 tokens consumed.
  - Added REST endpoint `GET /api/rfx/{id}/artifacts` to fetch stored graphs and reports.
  - Added "Saved Graphs & Reports (0 tokens)" instant viewer drawer in `chatbot/analyst.html`.
- **UI Tooltips & Cleanliness**:
  - Added informative Vuetify tooltips (`<v-tooltip>`) across all Decision Analyst tabs.
  - Hidden the Award Scenarios tab from the UI to streamline buyer workflows.
- **Ready-Made Comparison Widgets & Response Coverage (Standard SQL - No AI)**:
  - Added 5 deterministic SQL widgets directly in `backend/app/analyst/tools/comparison.py` and `chatbot/analyst.html`:
    1. *Overall Basket KPI Banner* (Target Spend, Optimal Basket Spend, Potential Savings Amount/%, Full Quote count vs Partial Response count).
    2. *Vendor Response Coverage Breakdown* (Total vendors, Full Quotes on 100% of line items vs Partial Quotes with coverage %, confident quotes, and spend per vendor).
    3. *L1 Best Price Summary & Savings* (Lowest quoted prices vs target prices per item).
    4. *Price Spread & Bidder Variance* (Min, Max, Absolute Spread, Spread % per line item).
    5. *Vendor Win Count Leaderboard* (Items won L1 and spend share).
- **Dynamic RFX Selector & Quick Chips**:
  - Replaced manual text input with a dynamic `<v-select>` dropdown and quick RFX switcher chip bar querying `GET /api/rfi`.
  - Displays rich RFX metadata (ID, title, category, uppercase status chip) and triggers automatic asynchronous reload of RFI specifications, comparison grids, trust audits, and cached artifacts upon selection.
- **RFI Details & Line Items Specification Tab (`chatbot/analyst.html`)**:
  - Dedicated *RFI Details & Items* tab (`<v-tab value="rfi">`) in the Analyst workbench.
  - Displays structured overview banner with commercial terms (Payment Terms, Delivery Terms, Validity & Response Deadline, Estimated Target Budget, Scope notes).
  - Quick action buttons to navigate directly from RFI details into AI Chat Assistant ("Ask AI About RFI") and comparison matrix ("Compare Vendor Bids").
  - Live client-side keyword search filter (`rfiItemSearch`) to filter line items by description, product code, material, item number, or specifications.
  - Renders complete line item specifications table with item numbering, descriptions, item codes, quantities with unit chips, material chips, formatted dimensions (`L × W × H unit`), unit target prices in INR, total target values, required dates, specifications, and total summary aggregation row.
  - URL query parameter auto-initialization (`?rfx_id=...` / `?id=...` / `?tab=...`) to immediately focus on the specified RFX and tab upon landing.
  - Automatically loads and refreshes `/api/rfi/{id}` on RFX selection or manual refresh.
- **Step 5: Comparison Grid Fixes & Vendor Comparison Redesign**:
  - **Comparison Grid Table Visibility & Reliability**:
    - Resolved template crashes caused by unsafe numerical method calls (`.toLocaleString()`) on null/undefined properties with centralized formatters (`formatCurrency`, `formatNumber`, `formatPct`).
    - Added reactive loading indicators (`loadingGrid`), error states (`gridError`), and empty state messaging when an RFX has no quotations.
    - Robust rendering across all 5 comparison table views: Side-by-Side Matrix, L1 Best Price Summary & Savings, Price Spread & Bidder Variance, Vendor Response Coverage Breakdown, and Vendor Win Leaderboard.
  - **Vendor Comparison Redesign (formerly Trust & Risk)**:
    - Renamed Tab 3 to **Vendor Comparison** across UI tab bars, headers, and tooltips.
    - Converted single-card layout into a clean, unified data table where **each vendor is exactly one line item**.
    - Enhanced `VendorTrustSummary` model and `compute_trust_report` tool with `score_reasoning` (transparent points breakdown of coverage, price OCR confidence, compliance, and flag deductions), `risk_level` (Low / Medium / High Risk), and `total_quoted_spend_inr`.
  - **Decision-Support Scope Boundary**:
    - The chat assistant and analyst screens function strictly as **decision-support and evaluation tools** for buyers to compare quotations and simulate allocation scenarios.
    - The system does not execute binding line item awards or change RFX lifecycle status to awarded; formal award issuance and status changes remain future scope.

## Step 6: Commercial Terms Filtering & Dynamic Line Item Operations
- **Commercial Terms & Free-Form Notes Auto-Separation**:
  - Resolved issue where pasted commercial terms sections (e.g. `Mandatory Commercial Terms To Include:`, `Clear Indication of MOQ`, `Freight / Shipping Cost`, `Warranty SLA`), conversational email paragraphs (e.g. greetings `Hi team`, closing paragraphs with implicit conditions `Please ensure delivery is completed within 15 days...`, `Thanks and regards`), and spreadsheet remarks/footer rows (`Note: GST 18% extra...`) were erroneously parsed as line items.
  - Implemented `is_separator_line()`, `is_conversational_or_boilerplate()`, `is_commercial_term_or_header()`, and natural language `extract_commercial_terms()` in `backend/app/intake/validation.py`.
  - Automatically isolates non-product sentences/notes, strips leading list markers (`1. `, `2) `, `[3] `), and maps implicit terms (`payment_terms`, `delivery_terms`, `validity_days`, `currency`, `scope`/`notes`) directly to RFI metadata.
  - Applied consistently across PydanticAI parser instructions, deterministic extractor (`backend/app/intake/agent.py`), Excel/CSV importer (`backend/app/intake/excel_parser.py`), and Master agent orchestration (`backend/app/agents/master.py`).
- **Dynamic Natural Language Line Item Operations**:
  - Added support for batch range deletions (e.g., "remove items 33 to 39", "remove 33-39", "delete items from 33 to 39").
  - Added support for comma-separated multiple item deletions (e.g., "remove items 2, 4, 6", "delete 1, 3").
  - Added support for relative item deletions (e.g., "remove last item", "delete last 7 items").
  - Added support for name/code removals (e.g., "remove [Pkg-029]", "remove brown tape").
  - Added support for natural language item updates (e.g., "update item 1 quantity to 1500", "change item 2 price to 20").
  - Enhanced `RFIRepository.remove_items()` and `RFIRepository.update_item()` with atomic batch execution and automatic contiguous `1..N` re-indexing in both PostgreSQL (`ROW_NUMBER()`) and memory fallback.
- **Direct Intake Intent Routing**:
  - Direct packaging text inputs (even without typing "Create an RFI" first) are recognized via packaging domain heuristics and routed directly to `handle_rfi_workflow_turn()`.

