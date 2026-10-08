# Implementation Plan: Tabular & Key-Value Intake Parsing and Solicitation Filtering

## 1. Problem Statement
When users paste structured text or tabular data from emails/spreadsheets containing key-value pairs (e.g. `1 [Pkg-001] 3-Ply Corrugated Box () Category: Cartons Target Qty: 433 Piece Baseline: ₹19.57`) alongside solicitation preambles (e.g. `Bidding Vendors are requested to provide itemized rates for the following Packaging Consumables:`):
1. Solicitation sentences are mistakenly parsed as product line items, corrupting the RFI Title (e.g., `Title: RFI for Bidding Vendors are requested to provide... and 30 other items`).
2. Table header lines (`# Description Quantity Dimensions Material / Specs`) are not filtered out.
3. Key-value tokens in rows (`Category: ...`, `Target Qty: ...`, `Baseline: ...`) are left inside the description or misassigned to columns.
4. Dimensions in item names (e.g. `(48Mm X 50M)`, `(1200X800Mm)`, `(18X24 In)`) and empty parens `()` need clean dimension extraction and description normalization.
5. Payment terms with single-character noise (e.g. `Payment Terms: S`) must be sanitized.

## 2. Proposed Changes

### Phase 1: Header & Solicitation Filtering in `backend/app/intake/validation.py`
- Add solicitation & RFP preamble patterns to `is_conversational_or_boilerplate()` and `is_commercial_term_or_header()`:
  - `bidding vendors are requested to...`
  - `vendors are requested to...`
  - `please provide itemized rates for...`
  - `the following packaging consumables are required...`
  - Table header patterns: `#\s*(?:item\s*)?description\s*(?:category|quantity).*`
- Sanitize `extract_commercial_terms()`:
  - Ensure `payment_terms` is at least 3 characters and not a stray single letter (e.g. `"S"`).

### Phase 2: Key-Value & Dimension Extraction in `_parse_single_packaging_clause()` in `backend/app/intake/agent.py`
- Extract explicit key-value annotations from the clause:
  - `Category:\s*([a-zA-Z0-9\s/&]+)` -> sets category and material.
  - `Target Qty:\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*([a-zA-Z]+)?` -> sets `quantity` and `unit`.
  - `Baseline:\s*(?:₹|Rs\.?|INR|\$)?\s*(\d+(?:,\d+)*(?:\.\d+)?)` -> sets `target_price`.
- Extract dimensions from 2D specs (e.g. `48mm x 50m`, `1200x800 mm`, `18x24 in`, `8x10 in`, `4x6 in`) in addition to 3D specs.
- Strip `Category: ...`, `Target Qty: ...`, `Baseline: ...`, and trailing/empty parentheses `()` from `item_description`.

### Phase 3: RFI Card Formatting & Baseline Display in `backend/app/agents/master.py`
- In `_format_rfi_card()`, ensure Material / Specs column displays the clean material and baseline target price (e.g. `Cartons | Baseline: ₹19.57` or `Cartons (Baseline: ₹19.57)` if target price is present).

### Phase 4: Verification & Test Suite
- Add comprehensive test in `backend/tests/test_chat_rfi_workflow.py` with the 30-item text from the user prompt.
- Run full test suite and save logs to `artifacts/logs/test_full_suite.log`.
- Update `README.md`, `architecture.md`, and `AI_context.md`.
