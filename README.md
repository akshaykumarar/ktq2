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

- **PydanticAI**: Primary agent & orchestration layer for packaging requirement extraction, classification, and normalization.
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

> **Note**: If database or LLM API keys are offline, the system automatically uses mock and memory fallback mechanisms for instant, uninterrupted test runs and local demos.

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
- `GET /api/rfi/{id}`: Retrieve RFI metadata, commercial terms, and line items.
- `PATCH /api/rfi/{id}`: Safely update editable fields (`title`, `terms`, `status`, `validity_days`, `response_deadline`).
- `POST /api/rfi/{id}/trigger`: Validates readiness, transitions status to `TRIGGERED`, records `triggered_at` timestamp.

### 5. Conversational RFI Workflow (`POST /api/chat` & `/ui`)
- **Chat Outcome Loader**: Chat UI (`/ui`) displays an animated processing loader bubble in the chat view and input area whenever an outcome is expected.
- **RFI Initial Input Flow**: When selecting "Create an RFI" from the welcome cards or user input, the assistant guides the user to supply packaging details instead of prematurely creating an empty dummy RFX.
- **Multi-Line Item Extraction**: Seamlessly extracts multiple packaging requirements from a single prompt (e.g. `1000 corrugated boxes, 300 x 200 x 150 mm, and 300 rolls of brown tape`) into multiple distinct line items in `ktq.rfx_items`.
- **Requirement Extraction**: Parses dimensions (L x W x H), quantity, delivery destination, and delivery date into structured line items, creates draft records in `ktq.rfx`, and outputs the formatted RFI solution and progress.
- **Strict Guardrails**: When in the RFI creation workflow, only solutions and progress for creating the RFI are provided. Checking existing RFI status or fetching external information (vendor directories, general questions) is politely discouraged and redirected back to RFI creation.
- **RFI Progression**: Users can review line items, amend terms, or say `"Trigger RFI"` to issue quote requests to qualified suppliers.

---

## Configuration

Agent and model configurations are decoupled from Python code:
- `config/llms.yaml`: LLM presets (`reasoning`, `fast`).
- `config/agents.yaml`: Agent definitions (`master`, `rfx`, `vendor`, `status`, `rfi_parser`).

---

## Running Tests

Run the full pytest suite:

```bash
.venv/bin/pytest backend/tests -v
```

Test logs are output to [`artifacts/logs/test_run.log`](file:///Users/akshaykumar/code/ktq2/artifacts/logs/test_run.log).
