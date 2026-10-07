# Phased Implementation Plan: RFI Details & Line Items Tab in Decision Analyst

## Problem Statement
The user requested: "show the line items of RFI in rfi details and items tab in analyst page".
The Decision Analyst UI (`chatbot/analyst.html`) requires complete, robust, and intuitive visualization of the selected RFI specifications and line items in the dedicated "RFI Details & Items" tab (`<v-tab value="rfi">`).

## Current Architecture & Implementation Review
1. **REST Endpoints**:
   - `GET /api/rfi`: Lists all RFIs for dropdown selectors.
   - `GET /api/rfi/{id}`: Returns complete RFI details with `items` list containing `item_number`, `description`, `item_code`, `quantity`, `unit`, `material`, `dimensions`, `target_price`, `currency`, `required_date`, `specifications`.
2. **Frontend UI Workbench (`chatbot/analyst.html`)**:
   - Tab 0: `<v-tab value="rfi">` "RFI Details & Items".
   - Structured RFI Header & Metadata overview card.
   - Key Commercial & Fulfillment Terms breakdown (Payment Terms, Delivery Terms, Validity & Response Deadline, Estimated Target Budget).
   - Line items specification data table displaying item numbers, descriptions, product codes, quantities with units, materials, dimension breakdown (`L × W × H unit`), unit target prices (₹), line total target values (₹), required delivery dates, specifications, and total summary aggregation row.

## Phase 1: Robustness & Feature Enhancements
1. **URL Query Parameter Auto-Selection**:
   - Support `?rfx_id=...` / `?id=...` / `?rfi_id=...` in `window.location.search` to automatically select and load the target RFI upon page navigation.
2. **Line Item Filter / Quick Search**:
   - Add a quick filter search bar within the Line Items specification card so buyers can filter long RFI item lists by keyword/description/material/code.
3. **Specification & Attribute Formatting**:
   - Ensure clean dimension formatting whether stored as structured dict, JSON string, or plain text.
   - Add chips for item status, materials, and quantities for quick visual scanning.
4. **Summary Aggregation & Safe Math**:
   - Safe parsing and computation of total quantity and total target spend across all items.
5. **Direct Navigation & Tab Switching**:
   - Allow direct link or tab switching between RFI specifications, chat assistant, comparison grid, and risk audits.

## Phase 2: Verification & Testing
1. Run full test suite (`PYTHONPATH=. .venv/bin/pytest tests/ backend/tests/ -v`).
2. Verify test output logs saved in `artifacts/logs/`.
3. Update documentation (`README.md`, `architecture.md`, `AI_context.md`).
