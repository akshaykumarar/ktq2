# Configurable Multi-Agent Procurement Assistant & Packaging RFI Workflow

A configurable procurement product powered by **FastAPI**, **PydanticAI**, and **PostgreSQL**, connected to the existing **AI-QL Chat UI** frontend and supporting direct end-to-end Packaging RFI procurement workflows.

## Architecture

```text
User / Warehouse Executive
    │
    ├── Plain Text Intake: POST /api/rfi/intake ────────────────┐
    │                                                           │
    ├── Excel Spreadsheet: POST /api/rfi/intake/excel ──────────┼──► [PydanticAI / Rule Extractor]
    │                                                           │          │
    └── Chat UI Frontend: POST /api/chat ───────────────────────┘          ▼
                                                                  [Pydantic Validation & Defaults]
                                                                           │
                                                                           ▼
                                                                  [PostgreSQL ktq.rfx / rfx_items]
                                                                           │
                                                                  ┌────────┴────────┐
                                                                  ▼                 ▼
                                                            PATCH /api/rfi/{id}   POST /api/rfi/{id}/trigger
                                                            (Edit terms/status)   (DRAFT/READY -> TRIGGERED)
```

- **PydanticAI & LLM-First Decision Engine**: LLM agents (`master`, `rfi_parser`, `rfx`, `vendor`, `status`) serve as the primary, first-choice engines for intent understanding, requirement extraction, conversational routing, and decision support, with deterministic fallbacks for offline testing.
- **WrenAI**: Preserved as secondary intelligence layer for semantic database analytics and natural language business intelligence queries over PostgreSQL.
- **PostgreSQL**: Source of truth storing RFIs (`ktq.rfx`), line items (`ktq.rfx_items`), and audit history (`ktq.rfx_activity`) under the dynamically configured schema (`DB_SCHEMA`, default `ktq`).
- **Pydantic Models**: Canonical, type-safe contracts across parsing, AI extraction, REST APIs, and database persistence.

---

## Quickstart & Setup

### 1. Prerequisites
- Python 3.10+ (tested with Python 3.14)
- Virtual environment (`.venv`)

### 2. Installation

```bash
# Activate virtual environment
source .venv/bin/activate

# Install requirements
pip install fastapi uvicorn "pydantic>=2.10" pyyaml python-dotenv httpx pytest pydantic-ai "psycopg[binary]" openpyxl
```

### 3. Environment Secrets

Copy `.env.example` to `.env` and configure secrets:

```bash
cp .env.example .env
```

Database & provider settings:
```dotenv
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434

# PostgreSQL Configuration
POSTGRES_MODE=cloud
DB_HOST=ep-muddy-voice-aozlceqy-pooler.c-2.ap-southeast-1.aws.neon.tech
DB_PORT=5432
DB_NAME=neondb
DB_USER=neondb_owner
DB_PASSWORD=your_password
DB_SSL_MODE=require
DB_CHANNEL_BINDING=require
DB_SCHEMA=ktq
# Alternatively:
# DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
```

> **Note**: Missing LLM API keys raise an explicit `ValueError` by default to ensure credentials are properly configured. For offline testing or unit tests, mock models can be explicitly configured via `provider: "test"` or `fallback_to_mock=True`. Database operations continue to support in-memory fallback for local test runs when PostgreSQL is unconfigured.

### 4. Running the Application

Start the unified FastAPI backend:

```bash
.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then visit:
- **Chat UI**: [http://localhost:8000/ui/](http://localhost:8000/ui/)
- **Interactive OpenAPI Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **System Health**: [http://localhost:8000/health](http://localhost:8000/health)
- **Database Health**: [http://localhost:8000/health/db](http://localhost:8000/health/db)

---

## Packaging RFI API Endpoints

### 1. Health Checks
- `GET /health` (or `/api/health`): Verifies running service and initialized agents.
- `GET /health/db` (or `/api/db/health`): Verifies PostgreSQL connectivity, schema existence, and query execution without leaking credentials.

### 2. Text Requirement Intake
- `POST /api/rfi/intake`
  - Accepts raw text requirements (e.g. `"Need 5000 boxes, 600x400x300 mm, 5 ply kraft, delivery by 30 Nov."`).
  - Extracts structured items, dimensions, plies, material, required date, and target price.
  - Identifies missing critical fields without hallucinating them.
  - Optionally creates an RFI immediately if `create_rfi=true`.

### 3. Excel Spreadsheet Intake
- `POST /api/rfi/intake/excel` (multipart/form-data)
  - Accepts `.xlsx` or `.csv` workbooks.
  - Tolerant header mapping (`qty`, `volume`, `quantity` → `quantity`; `specs`, `notes` → `specifications`).
  - Captures row-level errors without failing entire file.
  - Supports multi-sheet extraction.

### 4. RFI Lifecycle & Management
- `POST /api/rfi`: Direct creation of an RFI with structured requirements.
- `GET /api/rfi`: Retrieve list of all available RFIs/RFXs for dropdown selectors.
- `GET /api/rfi/{id}`: Retrieve RFI metadata, commercial terms, and line items.
- `PATCH /api/rfi/{id}`: Safely update editable fields (`title`, `terms`, `status`, `validity_days`, `response_deadline`).
- `POST /api/rfi/{id}/trigger`: Validates readiness, transitions status to `TRIGGERED`, records `triggered_at` timestamp.

### 5. Conversational RFI Workflow (`POST /api/chat` & `/ui`)
- **Chat Outcome Loader**: Chat UI (`/ui`) displays an animated processing loader bubble in the chat view and input area whenever an outcome is expected.
- **RFI Initial Input Flow**: When selecting "Create an RFI" from the welcome cards or entering packaging requirements directly, the assistant guides the user and initializes draft creation without creating empty dummy RFX records.
- **Commercial Terms & Unstructured Notes Auto-Separation**: Separator lines, commercial clause blocks, conversational email paragraphs (greetings, closing remarks, delivery instructions, quality/sample requirements), and Excel footer note rows (e.g. `Note: GST 18% extra. Rates must include transit insurance`) are automatically parsed into RFI terms metadata (`payment_terms`, `delivery_terms`, `validity_days`, `currency`, `scope`/`notes`) rather than becoming dummy line items. Numbered list prefixes (`1. `, `2) `) are stripped cleanly.
- **Dynamic Natural Language Line Item & Terms Operations**:
  - **Commercial Terms Updates & Resets**: Direct natural language modification of terms (e.g. `"payment terms update it to 10 days after delivery"`, `"delivery terms update it to Pune plant (DDP)"`, `"change currency to USD"`, `"update quote validity to 45 days"`, `"remove payment terms"`). Terms resets immediately revert fields to default parameters without deleting product line items.
  - **Multi-Item & Compound Modifications**: Supports updating multiple items and multiple attributes in a single natural language sentence (e.g. `"update quantity of 1 to 350, and 2 to 300 and 3 to 500 with baseline of ₹80"`).
  - **Flexible Phrasing & Prefixes**: Handles natural language variations (`"update quantity of 1 to 350"`, `"update line item 1 quantity to 300"`, `"update Pkg-001] 3-Ply Corrugated Box () to 300 pieces"`, `"with baseline of ₹80"`).
  - **Negative Confirmation Precedence**: Commands like `"no, update existing item"` reject duplicate additions and execute item modifications directly.
  - **Command Fallthrough Protection**: Commands, prepositions, or update instructions are never mistakenly converted into bogus line items.
  - **Batch Range Deletions**: `remove items 33 to 39`, `remove 33-39`, `delete 1-5`, `delete items from 3 to 5`.
  - **Multi-Item Lists**: `remove items 2, 4, 6`, `delete 1, 3`, `remove item 2 and item 4`.
  - **Relative Removals**: `remove last item`, `delete last 7 items`.
  - **Product Names/Codes**: `remove [Pkg-029]`, `remove brown tape`.
  - **Field Updates**: `update item 1 dimensions to 450x350x250 mm`, `change item 1 material to 7 ply heavy kraft`, `update item 1 quantity to 1500`, `change item 2 target price to 20 INR`, `update item 1 delivery date to 2026-12-01`.
  - **Automatic Re-indexing**: Removed items trigger consecutive re-indexing (`1..N`) for all remaining line items.
- **Duplicate Detection & Confirmation**: If an incoming requirement matches an item already in the draft, the assistant flags the duplicate and prompts the user for explicit confirmation before proceeding.
- **Advanced Natural Language & Tabular Intake**:
  - Robustly parses compound requirements, metric & imperial cube box dimensions (`50inch cube boxes 300`), film specs (`5m stretch films 1000 pcs`), and weights (`bubble wrap 50kg`).
  - Automatically parses pasted tabular and key-value rows (e.g. `1 [Pkg-001] 3-Ply Corrugated Box () Category: Cartons Target Qty: 433 Piece Baseline: ₹19.57`), mapping `Category`, `Target Qty`, `Baseline`, and dimensions (2D/3D) into structured fields.
  - Excludes RFP solicitation preambles and table headers from line items and titles.
  - Sanitizes commercial terms (e.g. rejects single-character bullets like `Payment Terms: S`).
- **Strict Guardrails**: When in the RFI creation workflow, only solutions and progress for creating the RFI are provided. Checking existing RFI status or fetching external information (vendor directories, general questions) is politely discouraged and redirected back to RFI creation.
- **RFI Progression**: Users can review line items, amend terms, or say `"Trigger RFI"` to finalize and issue quote requests to qualified suppliers.

---

## Step 2: Vendor Quotation Intake, OCR & Extraction Endpoints

### 1. Vendor Response Submission (Multipart / Asynchronous)
- `POST /api/vendor-responses` (Status: `202 Accepted`)
  - Accepts vendor quotation in **ANY** shape (Excel, CSV, PDF, Word, Email `.eml`, Images/Photos, raw text, JSON payload).
  - Immediately stores raw files/payloads byte-for-byte with SHA-256 idempotency before async processing.
  - Returns `{ "response_id": ..., "status": "received", "rfx_resolution_preview": ... }`.

#### Request Parameters (Multipart Form-Data):
- `files`: (0..n files) `.xlsx`, `.xls`, `.csv`, `.pdf`, `.docx`, `.doc`, `.txt`, `.eml`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.heic`
- `body_text`: Raw email body or pasted quote text
- `body_json`: Arbitrary JSON quotation payload
- `subject`: Email subject or submission title
- `sender_email`: Email address of the vendor representative
- `sender_name`: Name of the vendor contact or company
- `channel`: `email` | `upload` | `api` | `simulated` (default: `upload`)
- `received_at`: Timestamp (ISO format, defaults to current time)
- `rfx_ref`: Optional RFx reference code / number (e.g. `RFQ-2026-001` or `1`)
- `vendor_name`: Optional explicit vendor company name

#### cURL Example:
```bash
curl -X POST http://localhost:8000/api/vendor-responses \
  -F "files=@samples/Apex_Packaging_Quote.xlsx" \
  -F "sender_email=sales@apexpackaging.in" \
  -F "sender_name=Apex Packaging" \
  -F "subject=Quotation for RFQ-2026-001" \
  -F "rfx_ref=RFQ-2026-001" \
  -F "channel=email"
```

### 2. Response Status & Inspection
- `GET /api/vendor-responses/{id}`: Detailed status, per-document processing progress, RFx & vendor resolution with confidence scores and reasoning, summary counts.
- `GET /api/vendor-responses/{id}/items`: Extracted line items with matching state (`MATCHED`, `EXTRA`, `ALTERNATE`, `NOT_QUOTED`), validation state (`CONFIDENT`, `REVIEW`, `MISSING`), flags, reasoning, and crop coordinates.
- `GET /api/vendor-responses/{id}/documents/{doc_id}/raw`: Retrieve original document byte-for-byte exactly as received.
- `GET /api/response-items/{item_id}/crop`: Cropped PNG evidence image from PDF/image pages with 10% bounding padding.
- `GET /api/rfx/{rfx_id}/responses`: All vendor responses for an RFx with coverage summary, flags, and item comparisons.
- `POST /api/vendor-responses/{id}/reprocess`: Re-run the real extraction & validation pipeline (new run created, previous run preserved).
- `PATCH /api/response-items/{item_id}`: Human buyer correction audit trail (records before/after diffs, user, and timestamp).

---

## Step 3: Natural Language Analysis, Decision Support & Award Optimization

Step 3 equips buyers with natural language analysis, original RFI specification review, side-by-side comparison grids, trust & risk scoring, deterministic award optimization, Chart.js visual analytics, and export packs (XLSX).

### Decision Analyst Workbench (`/ui/analyst.html`)
- **RFI Details & Items Tab**: View the complete original RFI scope, commercial terms (payment terms, delivery terms, validity, response deadline), estimated target spend, live line items search filter, and full line item specifications table (dimensions, materials, quantities, target prices, total line values, required dates, specifications, and total summary row). Includes quick action buttons to jump directly into the AI Chat assistant or vendor comparison grid.
- **Analyst & Vendor Chat Tab**: Natural language AI assistant with 0-token caching for repeated questions, Chart.js diagrams, and transparent *How I Got This* step audit traces.
- **Comparison Grid & Widgets Tab**: Side-by-side line item comparison matrix and ready-made SQL benchmark widgets with L1 pricing, price spreads, and evidence crops.
- **Trust & Risk Tab**: Vendor trust scores (0-100), risk audits, quotation confidence levels, and money-at-risk breakdown.

### REST Endpoints

1. **`POST /api/analyst/ask`**: Natural language Q&A returning structured JSON response contract:
   - Request: `{ "rfx_id": 6, "session_id": "sess_1", "question": "Split the award: cheapest per line" }`
   - Response: `{ answer_text, tables, charts, exports, caveats, how_i_got_this, confidence, suggested_followups, trace_id }`
2. **`GET /api/analyst/sessions/{id}`**: Conversation history and telemetry traces.
3. **`POST /api/analyst/feedback`**: Submit rating, comments, and corrected SQL for any trace.
4. **`GET /api/rfx/{id}/comparison`**: Side-by-side comparison matrix (items x vendors: prices, state, flags, crops, coverage row, spend totals).
5. **`GET /api/rfx/{id}/trust`**: Comprehensive trust & risk profile (0-100 score, % coverage, CONFIDENT/REVIEW/MISSING quotes, money at risk).
6. **`POST /api/rfx/{id}/award/scenarios`**: Create / run deterministic allocation scenario simulation (Decision Tool).
7. **`GET /api/rfx/{id}/award/scenarios/{sid}`**: Fetch saved scenario.
8. **`POST /api/rfx/{id}/award/scenarios/{sid}/finalize`**: Saves scenario audit record. Note: Actual RFX status change to awarded is future scope.
9. **`GET /api/exports/{id}`**: Download multi-tab Excel workbooks with mandatory *Assumptions & Caveats* sheet.

### cURL Examples

#### 1. Ask a Decision Question (with 0-token caching for repeated questions):
```bash
curl -X POST http://localhost:8000/api/analyst/ask \
  -H "Content-Type: application/json" \
  -d '{
    "rfx_id": 6,
    "session_id": "buyer_session_1",
    "question": "Who is cheapest vendor for each item?"
  }'
```

#### 2. Fetch Comparison Grid with Ready-Made SQL Widgets:
```bash
curl -X GET http://localhost:8000/api/rfx/6/comparison
```
*Returns side-by-side matrix along with deterministic SQL widgets: `basket_summary` (with full vs partial quote counts), `vendor_coverage` (100% quoted vs partial responses per vendor), `l1_items`, `price_spread`, and `vendor_leaderboard`.*

#### 3. Fetch Vendor Comparison & Trust Audit:
```bash
curl -X GET http://localhost:8000/api/rfx/6/trust
```
*Returns vendor reliability scores, deterministic point calculations (`score_reasoning`), risk tier (`risk_level`), price confidence breakdown, money at risk, and total quoted spend per vendor.*

#### 4. Fetch Stored Graphs & Reports (0 Tokens):
```bash
curl -X GET http://localhost:8000/api/rfx/6/artifacts
```

#### 4. Finalize Award Scenario with Flag Acceptance:
```bash
curl -X POST http://localhost:8000/api/rfx/6/award/scenarios/1/finalize \
  -H "Content-Type: application/json" \
  -d '{
    "accepted_review_flags": [
      {"vendor_id": 39, "item_id": 8, "reason": "Verified price manually via vendor email"}
    ],
    "buyer_name": "lead_procurement@company.com"
  }'
```

---

## Tuning Guide & Customization Knobs

All analytical knobs are externalized in config files (ordered by tuning impact):

1. **Semantic Model Descriptions (`config/wren_semantic_model.yaml` & `config/wren_mdl.json`)**:
   - *Impact*: **Highest**. Explaining column semantics (e.g. `effective_price_inr`, `state`, `review_status`) directly guides SQL generation accuracy.
   - *Deployment*: Run `python scripts/deploy_wren_model.py`.
2. **Few-shot Question-to-SQL Examples (`config/wren_examples.yaml`)**:
   - *Impact*: **Very High**. 20+ seed pairs covering joins, aggregations, knockouts, and ranking.
   - *Feedback Sync*: Run `python scripts/export_feedback_to_examples.py` to convert rated buyer corrections into new examples.
3. **Analyst Orchestrator System Prompt (`config/prompts/analyst_system_v1.txt`)**:
   - *Impact*: **High**. Enforces comparability-first, numbers verification, and structured markdown narration.
4. **Guardrail & Threshold Knobs (`config/analyst_config.yaml`)**:
   - *Impact*: **Medium**. Configures allowed view allowlists, statement timeout (`5000ms`), max tool steps, trust penalties, and default what-if assumptions (FX rates, freight).
5. **Model Stage Switcher (`config/llms.yaml` & `config/analyst_config.yaml`)**:
   - *Impact*: **Medium**. Seamlessly switch providers (`openai`, `anthropic`, `google`, `ollama`) across pipeline stages.

---

## Running Benchmarks & Tests

### Run 33-Question Decision Analyst Benchmark:
```bash
PYTHONPATH=. .venv/bin/python scripts/eval_analyst.py
```
Outputs pass/fail matrix and logs trace details to `artifacts/logs/eval_analyst.log`.

### Run Full Test Suite (69 Tests):
```bash
PYTHONPATH=. .venv/bin/pytest tests/ backend/tests/ -v
```

