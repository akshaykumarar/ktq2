# Implementation Plan - Packaging RFI End-to-End Workflow

## Overview
Build the end-to-end Packaging RFI workflow:
**Text / Excel input → parse requirements → AI agent structures requirements → create RFI → save/update DB → allow RFI triggering and status updates**

The architecture leverages:
- **PydanticAI:** Dedicated agent for packaging requirement extraction, classification, and normalization from natural language or unstructured text.
- **FastAPI:** Clean, documented REST API for health, intake, Excel parsing, RFI lifecycle operations.
- **PostgreSQL (`ktq` or configured `DB_SCHEMA`):** Source of truth storing RFI records (`rfx`), line items (`rfx_items`), supplier attachments, and status tracking.
- **WrenAI:** Preserved as secondary intelligence layer for semantic queries / analytics without blocking core intake.
- **Pydantic Models:** Type-safe domain models for line items, intake requests, RFI records, updates, and triggers.

---

## Architecture Flow

```text
User / Client
     │
     ├── Text Intake: POST /api/rfi/intake 
     │         │
     │         ▼
     │   [PydanticAI Requirement Extractor]
     │         │ (Structures requirements, identifies missing/ambiguous fields)
     │
     ├── Excel Intake: POST /api/rfi/intake/excel (multipart/form-data)
     │         │
     │         ▼
     │   [Excel Parser (openpyxl / fallback)]
     │         │ (Normalizes headers, extracts row-level packaging items)
     │
     ▼
[Pydantic Validation & Defaults Layer]
     │ (Applies non-hallucinatory defaults, separates deterministic checks)
     ▼
[RFI Lifecycle Service & DB Repository]
     │ (Respects dynamic DB_SCHEMA, handles parameterized transactions)
     ├── Create RFI: (DRAFT -> READY)
     ├── Get RFI: GET /api/rfi/{id}
     ├── Update RFI: PATCH /api/rfi/{id}
     └── Trigger RFI: POST /api/rfi/{id}/trigger (READY -> TRIGGERED)
```

---

## Phases of Execution

### Phase 1: Environment & Database Layer
1. **Schema & Connection Handling**:
   - Enhance `backend/app/config/settings.py` to support `DATABASE_URL` or structured `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSL_MODE`, and `DB_SCHEMA` (default: `ktq`).
   - Implement `backend/app/db/connection.py` providing a connection context manager that always executes `SET search_path TO {schema}, public` or qualifies tables.
2. **Database Health Check**:
   - Update `backend/app/db/health.py` to test connection, check schema existence, and run a simple query (`SELECT 1`).
   - Expose lightweight endpoints:
     - `GET /health` & `GET /api/health`
     - `GET /health/db` & `GET /api/db/health`
   - Graceful resilience: app starts even if DB is temporarily unreachable.
3. **Migration & Table Schema**:
   - Create `migrations/001_rfi.sql` to support RFI lifecycle statuses (`draft`, `ready`, `triggered`, `responses_pending`, `completed`), default sequences, and auditing columns.
   - Implement repository operations in `backend/app/db/repository.py` for RFI CRUD and state transitions with parameterized SQL and in-memory mock fallback when DB is disconnected.

### Phase 2: Pydantic Domain Models & PydanticAI Agent
1. **Domain Models (`backend/app/intake/models.py`)**:
   - `PackagingRequirement` / `PackagingLineItem`: category, item_description, quantity, unit, dimensions, material, specifications, required_date, target_price, notes.
   - `RFIStatus` enum: `DRAFT`, `READY`, `TRIGGERED`, `RESPONSES_PENDING`, `COMPLETED`.
   - `RFICreateRequest`, `RFIUpdateRequest`, `RFIDetailResponse`, `RFITriggerResponse`.
   - `TextIntakeRequest`, `IntakeExtractionResult`.
2. **PydanticAI Requirement Agent (`backend/app/intake/agent.py`)**:
   - Configure in `config/agents.yaml` under `rfi_parser`.
   - Uses `Agent[None, PackagingIntakeResult]` with structured response schema.
   - Extracts packaging items, dimensions, plies, material, required date, target price.
   - Identifies missing fields (e.g. quantity, dimensions) and ambiguity without hallucinating.
   - Supports offline `TestModel` fallback when LLM API keys are absent.
3. **Deterministic Defaults & Validation (`backend/app/intake/validation.py`)**:
   - Separates deterministic business logic from LLM output.
   - Checks packaging domain keywords vs non-packaging items.
   - Enforces explicit, non-hallucinated defaults (e.g. standard payment terms, delivery notes).

### Phase 3: Excel Intake Parser
1. **Excel Parser (`backend/app/intake/excel_parser.py`)**:
   - Supports `.xlsx` via `openpyxl`.
   - Flexible header aliases (e.g. `qty`, `volume`, `quantity` -> `quantity`; `spec`, `notes` -> `specifications`).
   - Row-level error reporting (skips or flags malformed rows without failing the entire file).
   - Generates normalized `PackagingLineItem` list with traceability to source sheet and row.

### Phase 4: RFI Lifecycle Endpoints & Service
1. **RFI Endpoints (`backend/app/api/rfi.py`)**:
   - `POST /api/rfi/intake` (text payload `{ "text": "..." }`).
   - `POST /api/rfi/intake/excel` (multipart upload).
   - `POST /api/rfi` (create RFI directly from structured requirements).
   - `GET /api/rfi/{id}` (fetch RFI with line items and current status).
   - `PATCH /api/rfi/{id}` (update editable fields with Pydantic validation).
   - `POST /api/rfi/{id}/trigger` (validate readiness, transition to TRIGGERED, record timestamp).
2. **Service Layer (`backend/app/intake/service.py`)**:
   - Coordinates parsing, AI extraction, validation, and repository persistence.
   - Ensures RFI lifecycle state machine rules are respected.

### Phase 5: Verification & Testing
1. **Automated Unit & Integration Tests**:
   - DB health and schema configuration tests (`test_db_health.py`).
   - Text parsing & PydanticAI extraction tests (`test_text_intake.py`).
   - Excel parsing tests with multi-sheet and header variations (`test_excel_intake.py`).
   - RFI CRUD and state transitions (`test_rfi_lifecycle.py`).
   - Mock LLM in tests so no live LLM key is needed.
2. **Documentation & Context Updates**:
   - Update `README.md`, `architecture.md`, `AI_context.md`.
   - Record test outputs in `artifacts/logs/`.
