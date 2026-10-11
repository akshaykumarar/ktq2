# Implementation Plan: Fix Table Rendering ("No records to display") & Clear Chat History

## 1. Problem Statement
1. **Table Rendering Bug**:
   In `chatbot/analyst.html`, tables in the AI Analyst chat displayed `"No records to display."` with no headers or data rows, despite displaying row count badges (e.g., `30 rows`, `4 rows`, `25 rows`).
   - **Root Cause**: The template used `<v-table density="compact" hover>` in an in-DOM Vue template. Browsers' native HTML5 parser encounters `<thead>`, `<tbody>`, `<tr>`, `<th>`, `<td>` inside a non-`table` custom element tag (`<v-table>`), which violates HTML5 table content models and causes the browser parser to discard/hoist table elements before Vue compiles the template.
2. **Chat History Clear Feature**:
   The user requested clearing past chat history. Currently, the Decision Analyst workbench has no "Clear Chat" button, and traces/artifacts accumulate without a reset mechanism.

## 2. Phased Implementation Plan

### Phase 1: Frontend Table Rendering Fix
- Replace `<v-table density="compact" hover>` in `chatbot/analyst.html` with standard `<table class="comparison-table">`.
- Apply defensive rendering on `tbl.columns || []` and `Array.isArray(r) ? r : Object.values(r)`.
- Ensure `:colspan="(tbl.columns && tbl.columns.length) ? tbl.columns.length : 1"` is properly bounded.

### Phase 2: Add Clear Chat Functionality (UI & Backend)
- **UI (`chatbot/analyst.html`)**:
  - Add a "Clear Chat" button to the chat header toolbar.
  - Implement `clearChat()` to empty `chatHistory`, generate a new `sessionId`, and call the delete endpoint.
  - Also provide an option/button in the UI to clear stored artifacts if desired.
- **Backend (`backend/app/analyst/repository.py` & `backend/app/api/analyst.py`)**:
  - Implement `delete_session_history(session_id)` and `clear_rfx_history(rfx_id)`.
  - Add `DELETE /api/analyst/sessions/{id}` and `DELETE /api/analyst/history/{rfx_id}`.
  - Invalidate `_RESPONSE_CACHE` and `_RFX_ARTIFACTS_CACHE`.

### Phase 3: Immediate Database Cleanup
- Execute cleanup query to delete existing traces and artifacts for RFx #25 and clear memory caches.

### Phase 4: Testing & Verification
- Unit test endpoints and repository methods in `tests/test_analyst_chat_history.py`.
- Run pytest suite and save test output to `artifacts/logs/test_table_rendering_and_clear_chat.log`.

### Phase 5: Documentation
- Update `AI_context.md`, `architecture.md`, and `README.md`.
