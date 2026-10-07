# Implementation Plan - Working RFI Creation Workflow & Chat Loader

## 1. Overview
Fix the multi-agent chat workflow so that:
1. When user inputs a message and an outcome is expected, a visual loader indicator appears in the chat stream during processing.
2. The welcome card offers "Create an RFI" as the initial input option.
3. When the user selects RFI as the initial input, the conversation enters a dedicated RFI creation workflow that guides requirement collection rather than prematurely creating an empty mock RFX.
4. When the user provides packaging requirements (e.g., "10 corrugated boxes, 300 x 200 x 150 mm, delivered to bangalore by November 15, 2026"), the system parses specs, dimensions, delivery date, location, and quantity, and creates/drafts the RFI, displaying full solution and progress.
5. In this RFI creation mode, if the user attempts to fetch any other information (vendor lookup, general info) or status of an existing RFI, the system does not encourage it and firmly redirects to completing the RFI creation.

---

## 2. Architecture & Design Decisions

### A. Frontend Loader (`chatbot/index.html`)
- Display a dedicated loading bubble in `<v-container class="chat-bot">` whenever `messageStore.generating` is true.
- Bubble contains assistant avatar, pulsating/circular loading spinner, and clear user-facing text ("Processing your request...").
- Update welcome screen card to label "Create an RFI" with payload `Create an RFI`.
- Maintain full reactivity with Vue 3 / Vuetify.

### B. Session Context & State Management (`backend/app/api/chat.py` & `backend/app/agents/`)
- Introduce session-aware state tracking per `conversation_id`.
- Track:
  - `initial_mode`: `"rfi_creation"` when initial prompt matches RFI initiation (`"Create an RFI"`, `"Create an RFX"`, `"RFI"`, etc.).
  - `active_rfi_id`: ID of the draft/created RFI in the session.
  - `collected_requirements`: Line items and terms collected so far.
- In `rfi_creation` mode:
  - Disallow / do not encourage status checks of existing RFIs.
  - Disallow / do not encourage external information fetching (vendor directory, non-RFI queries).
  - Guide requirement collection if no details provided yet.
  - Parse packaging details using `backend.app.intake.agent.extract_requirements`.
  - Persist to `RFIRepository` (with fallback to in-memory store) so a real RFI record with line items is created.
  - Present structured progress: RFI ID, Line Items (dimensions, quantity, material, dates, location), commercial terms, next steps.

### C. Fallback & Offline Resilience
- `execute_rfx_task` and `run_master_orchestration` must gracefully handle offline/sandboxed execution without generating dummy "Title: Create an RFX, Quantity: 1" responses.
- If user input is "Create an RFI" / "Create an RFX", prompt for requirements instead of creating an empty dummy record.
- If user provides packaging requirements, extract them and create the real draft RFI record.

---

## 3. Phased Implementation Steps

### Phase 1: Planning & Artifacts
- Create `artifacts/plan_rfi_workflow_fix.md`.
- Ensure `artifacts/logs/` directory exists for test logs.

### Phase 2: Frontend Loader & Welcome Card Update
- In `chatbot/index.html`:
  - Update card 1 title to "Create an RFI" and `selectOption('Create an RFI')`.
  - Add chat loader element inside the chat box view when `messageStore.generating` is true.
  - Ensure loader automatically clears when server response arrives.

### Phase 3: Backend Session & RFI Creation Flow Implementation
- In `backend/app/agents/rfx.py` & `backend/app/agents/master.py` & `backend/app/api/chat.py`:
  - Support `conversation_id` in orchestration.
  - Maintain conversation state per `conversation_id` (e.g. `_CONVERSATION_SESSIONS`).
  - Detect initial input "Create an RFI" / "Create an RFX" / "RFI":
    - Set session mode to `rfi_creation`.
    - Reply asking for packaging requirements.
  - When user provides requirements:
    - Use `extract_requirements` to extract dimensions, quantities, delivery date, location, specs.
    - Create/draft the RFI in `RFIRepository` (or DB).
    - Return formatted RFI summary with progress and solution.
  - When in `rfi_creation` mode and user asks for:
    - Existing RFI status -> return polite discouragement and redirect back to RFI creation.
    - Other info (vendors, general info) -> return polite discouragement and redirect back to RFI creation.

### Phase 4: Test Suite & Verification
- Add comprehensive pytest tests covering:
  - Initial RFI selection prompting for requirements.
  - Packaging requirement extraction creating real RFI with dimensions & quantity.
  - Discouraging existing RFI status checks during RFI creation flow.
  - Discouraging other information / vendor queries during RFI creation flow.
  - Ensuring loader and API contracts match expected types.
- Run all tests and record logs in `artifacts/logs/`.

### Phase 5: Documentation Updates
- Update `AI_context.md`, `README.md`, and `architecture.md` per Rule 2.
