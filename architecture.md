# System Architecture

## Overview
The application is a configurable multi-agent procurement platform integrating a FastAPI + PydanticAI backend with PostgreSQL, supporting the Vue 3 / Vuetify client-side application alongside an end-to-end Packaging RFI procurement workflow.

---

## Packaging RFI Workflow Architecture

```mermaid
flowchart TD
    subgraph Inputs["1. Intake Channels"]
        A1["Text Input (POST /api/rfi/intake)"]
        A2["Excel Upload (POST /api/rfi/intake/excel)"]
        A3["Chat UI (POST /api/chat)"]
    end

    subgraph ChatOrchestration["2. Chat Session & Focus Guardrails"]
        S1["Session Tracker (ChatSessionState)"]
        S2["RFI Focus Guardrail (Strict Creation Only)"]
        S3["Status / External Info Discourager"]
    end

    subgraph Processing["3. Parsing & AI Extraction"]
        B1["Openpyxl Spreadsheet Parser"]
        B2["PydanticAI Packaging Agent (rfi_parser)"]
        B3["Deterministic Packaging Extractor (fallback)"]
    end

    subgraph Governance["4. Validation & Business Rules"]
        C1["Non-Packaging Domain Filter"]
        C2["Missing Critical Fields Detector"]
        C3["Traceable Defaults Engine (Non-hallucinatory)"]
        C4["RFI Readiness Checker"]
    end

    subgraph Persistence["5. PostgreSQL Source of Truth"]
        D1["ktq.rfx (RFI Header & Lifecycle State)"]
        D2["ktq.rfx_items (Line Items, Dimensions, Specs)"]
        D3["ktq.rfx_activity (Audit & Trigger Timestamps)"]
    end

    subgraph Analytics["6. Intelligence & Analytics Layer"]
        E1["PydanticAI (Intake & Orchestration)"]
        E2["WrenAI (Semantic DB Analytics & NL-to-SQL)"]
    end

    A1 --> B2
    A1 -. fallback .-> B3
    A2 --> B1 --> C4
    A3 --> S1 --> S2
    S2 -->|Disallowed: Status / Info| S3
    S2 -->|Allowed: Specs / Progress| B2
    B2 --> C1
    B3 --> C1
    C1 --> C2 --> C3 --> C4
    C4 -->|Create RFI| D1
    D1 --- D2
    D1 --- D3
    D1 -.-> E2
```

---

## Core Modules & Design Decisions

### 1. Unified Backend (`backend/app/main.py`)
- Built on **FastAPI** with lifespan context for startup initialization of all agents.
- CORS middleware enabled for local UI and external origins.
- Static file serving mounted at `/ui` to serve `chatbot/index.html` directly.
- Health check endpoints:
  - `GET /health` and `GET /api/health`: General system and agent readiness.
  - `GET /health/db` and `GET /api/db/health`: Safe PostgreSQL connection, dynamic schema presence, and query verification without exposing credentials.

### 2. Conversational RFI Workflow & Guardrails (`backend/app/agents/master.py`)
- **Session Management (`ChatSessionState`)**: Tracks conversation mode (`is_rfi_workflow`), active RFI ID, and message turn count per `conversation_id`.
- **RFI Initial Input Flow**:
  - Starter command (`Create an RFI`) guides the user on required packaging details (item, dimensions, quantity, delivery location/date) without prematurely creating dummy 1-unit records.
  - Requirement intake (`"10 corrugated boxes, 300 x 200 x 150 mm..."`) extracts structured line items, creates draft records via `RFIRepository`, and outputs line item tables with commercial terms.
- **Strict Guardrails**:
  - Inquiries for status of existing RFIs (e.g. `What is the status of RFX-101?`, `Check vendor response`) are discouraged and redirected to completing RFI creation.
  - External inquiries (vendor lookups, directory searches, general questions) are discouraged and redirected to RFI creation.
- **UI Progress & Outcome Loader (`chatbot/index.html`)**:
  - Dynamic loader bubble and input field loading bar displayed while `generating == true`.
  - Welcome card configured with `Create an RFI`.

### 3. Database Layer & Dynamic Schema (`backend/app/db/`)
- **Dynamic Schema**: Does not hardcode schema names; uses the configured `DB_SCHEMA` (default: `ktq`).
- **Connection Isolation**: Connection context manager executes `SET search_path TO "{schema}", public` to ensure all queries resolve to the active tenant/environment schema.
- **Repository Abstraction (`RFIRepository`)**: Parameterized queries, atomic transactions for RFI + items creation, and an in-memory fallback for offline test isolation.
- **Tables**:
  - `ktq.rfx`: Master RFI record (`id`, `title`, `category`, `scope`, `currency`, `payment_terms`, `delivery_terms`, `validity_days`, `response_deadline`, `status`, `triggered_at`, `created_at`, `updated_at`).
  - `ktq.rfx_items`: Normalized line items (`item_number`, `description`, `quantity`, `unit`, `material`, `dimensions`, `specifications`, `target_price`, `currency`, `required_date`).
  - `ktq.rfx_activity`: Audit log (`rfx_id`, `activity_type`, `description`, `performed_by`, `created_at`).

### 4. PydanticAI Agent & Deterministic Extractor (`backend/app/intake/agent.py`)
- PydanticAI `rfi_parser` configured via `config/agents.yaml`.
- Produces strict `IntakeExtractionResult` and `PackagingRequirement` models rather than free-form text.
- **Multi-Line Item Support**: Robustly segments multi-item compound requirements (e.g., `1000 corrugated boxes, 300 x 200 x 150 mm, and 300 rolls of brown tape`) into multiple typed `PackagingRequirement` line items with independent dimensions, units, and materials.
- Extracts dimensions into structured dictionaries (`length`, `width`, `height`, `unit`).
- Deterministic regex fallback guarantees 100% test reliability and offline execution without requiring live API keys.
- **No Hallucination**: Missing quantities, dimensions, prices, or dates are flagged explicitly rather than invented.

### 5. Excel Intake Parser (`backend/app/intake/excel_parser.py`)
- Reads `.xlsx` via `openpyxl` and `.csv` via standard library.
- Flexible header alias dictionary handles variations (`qty`, `volume`, `quantity` → `quantity`; `specs`, `notes` → `specifications`).
- Captures row-level errors and skips blank or malformed lines without failing the entire file.

### 6. RFI Lifecycle & REST API (`backend/app/api/rfi.py`)
- Lifecycle states: `DRAFT` → `READY` → `TRIGGERED` → `RESPONSES_PENDING` → `COMPLETED`.
- `POST /api/rfi/intake`: Text requirements intake.
- `POST /api/rfi/intake/excel`: Spreadsheet requirement upload.
- `POST /api/rfi`: Direct RFI creation.
- `GET /api/rfi/{id}`: Full RFI detail retrieval.
- `PATCH /api/rfi/{id}`: Safe updates to permitted fields.
- `POST /api/rfi/{id}/trigger`: Validates readiness, transitions status to `TRIGGERED`, records `triggered_at`.

### 7. WrenAI Role & Intelligence Layer
- WrenAI is retained as a secondary intelligence layer for semantic analytics and natural-language reporting over PostgreSQL data (e.g., supplier response times, price variance by packaging category, pending RFIs).
- The primary intake workflow does not block on WrenAI, ensuring low-latency, resilient procurement operations.
