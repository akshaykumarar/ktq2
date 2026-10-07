# Implementation Plan: Dynamic RFX Dropdown Selector

**Task Goal:** Replace the manual/numeric RFX ID input in the Decision Analyst UI top app bar with a dynamic dropdown (`<v-select>` / `<v-autocomplete>`) that fetches available RFX IDs with titles, status, and categories from the database table (`ktq.rfx`), providing smooth automatic loading and item switching.

---

## 1. Objectives & Scope
1. **Backend List API**:
   - Add `list_all()` method in `RFIRepository` (`backend/app/db/repository.py`).
   - Add `list_rfis()` in `backend/app/intake/service.py`.
   - Add `GET /api/rfi` endpoint in `backend/app/api/rfi.py` returning list of RFX records (`id`, `title`, `status`, `category`, `currency`, `created_at`).
2. **Frontend Dynamic Dropdown Selector**:
   - In `chatbot/analyst.html`:
     - Replace `<v-text-field v-model.number="rfxId">` in the app bar with a `<v-select>` or `<v-autocomplete>` configured with dynamic items loaded from `GET /api/rfi`.
     - Display rich item labels (e.g. `RFX #6: Packaging Corrugated Boxes [COMPLETED]`).
     - Auto-select the first available RFX ID if none is set or if current ID is not in list.
     - Reload comparison grid, trust audits, and cached artifacts automatically when the selected RFX changes (`@update:model-value="loadAllData"`).
3. **Automated Verification**:
   - Add unit test for `GET /api/rfi` endpoint and `list_all()` repository method.
   - Run full regression test suite and save logs to `artifacts/logs/`.
4. **Documentation**:
   - Update `README.md`, `architecture.md`, and `AI_context.md`.
