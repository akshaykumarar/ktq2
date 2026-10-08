# Implementation Plan: RFI Tabular BOQ Import & UI Display Fix

## Problem Summary
When importing an Enterprise RFI/RFQ document containing header metadata, a tabular BOQ (30 line items with SKU codes, descriptions, categories, quantities, UOM, baseline prices, lead times), and commercial instructions:
1. **False-Positive Modification Intent**: Substring match on `"set"` within `"settlement"` in commercial instructions caused the entire 30-item text to be misclassified as a single-item modification command (`parse_modification_intent`).
2. **Session Bleed-Over (`_get_active_rfi_id`)**: `_get_active_rfi_id()` defaulted to querying `repo.list_all(limit=1)`, mutating existing historical RFIs rather than creating a new draft when starting a new document import.
3. **Tabular BOQ Parsing**: When structured tab-separated or column-aligned BOQ tables (e.g., `BOQ Item #`, `SKU Code`, `Item Description`, `Category`, `Target BOQ Quantity`, `Standard UOM`, `Internal Baseline (INR)`, `Target Lead Time (Days)`) are pasted, the text parser treated them as free-form text instead of parsing column-aligned table rows, leading to mangled descriptions, missed target prices, and misidentified quantities.
4. **Header & Footer Exclusion**: Document headers (`ENTERPRISE STRATEGIC SOURCING CELL...`, `RFI Reference: ...`, `Scope: ...`, `Baseline Currency: ...`) and footer instructions (`MANDATORY SOURCING & COMMERCIAL INSTRUCTIONS...`) were erroneously converted into bogus line items instead of extracting RFI metadata and filtering instructions from line items.
5. **Dimension Stripping & Clean Description Formatting**: Regex dimension cleaners corrupted descriptions with 2D dimensions like `(48mm x 50m)` into `(48mm x )`, and left trailing empty parentheses `()`.
6. **UI & Card Presentation**: Output markdown table columns must be properly aligned with SKU code, clean item description, exact quantities with UOM, extracted dimensions, and category/material with baseline prices.

---

## Phase 1: Robust Intent Detection & Session Isolation (`backend/app/agents/master.py`)
- **Word-Boundary Match for Intent Verbs**: Replace substring `v in q_lower` in `parse_modification_intent`, `parse_removal_intent`, and `parse_terms_intent` with strict word boundaries `\b(update|change|modify|set|adjust|edit)\b`.
- **Multi-Line & Tabular Guard**: If input contains multi-row tabular data (e.g. `\t`, `BOQ-`, `SKU`, `\n` with multiple items, or > 2 lines of specifications), do NOT treat it as a single-item edit/modification command.
- **Session-Scoped RFI Isolation**: Remove fallback to `repo.list_all(limit=1)` in `_get_active_rfi_id()`. If the user is importing a full RFI or has not explicitly bound to an active draft in the current session, create a new RFI draft.

---

## Phase 2: Tabular BOQ & Document Header/Footer Parser (`backend/app/intake/agent.py` & `validation.py`)
- **Document Header Metadata Extractor**:
  - Parse `RFI Reference: RFI-PKG-2026-001` -> set as RFI reference / title.
  - Parse `Scope: Annual Packaging Consumables Portfolio` -> set as RFI scope / category.
  - Parse `Target Validity: 90 Days` -> `validity_days = 90`.
  - Parse `Baseline Currency: INR (₹)` -> `currency = INR`.
  - Parse `Order Volume Scope: ...` -> record in scope notes.
- **Tabular BOQ Row Detection & Parser**:
  - Detect table header row (e.g., matching columns: `BOQ Item #`, `SKU Code`, `Item Description`, `Category`, `Target BOQ Quantity` / `Quantity`, `Standard UOM` / `UOM`, `Internal Baseline (INR)` / `Target Price`, `Lead Time`).
  - For each tabular line:
    - Extract SKU code (e.g., `PKG-001`, `[PKG-001]`).
    - Extract clean Item Description (e.g., `3-Ply Corrugated Box (10x8x6 in)`).
    - Extract Category (e.g., `Cartons`, `Tapes`, `Films`, `Cushioning`, `Pallets`, `Strapping`, `Protectors`, `Bags`, `Labels`, `Protection`).
    - Extract Quantity (e.g., `433`, `269`, `500`).
    - Extract UOM (e.g., `Piece`, `Metre`, `Roll`, `Kg`, `Pack`).
    - Extract Target Price / Baseline INR (e.g., `₹ 19.57` -> `19.57`, `₹ 1,451.02` -> `1451.02`).
    - Extract Lead Time / Required Date (e.g., `7` days -> calculate delivery date or store in specs).
    - Parse 2D/3D dimensions from description (e.g. `10x8x6 in` -> `{length: 10, width: 8, height: 6, unit: 'in'}`).
- **Footer Sourcing & Commercial Instructions Filtering**:
  - Automatically isolate `MANDATORY SOURCING & COMMERCIAL INSTRUCTIONS` and numbered condition bullets into `scope`/`notes` metadata rather than parsing them as line items.
- **PydanticAI LLM Agent & Deterministic Fallback Parity**:
  - Ensure both LLM parser prompt and deterministic parser handle tabular BOQ inputs cleanly without dropping fields.

---

## Phase 3: Dimension Preservation & Description Formatting
- Fix regex in `_parse_single_packaging_clause` and `_format_rfi_card` so descriptions retain readable names (e.g. `[PKG-001] 3-Ply Corrugated Box (10x8x6 in)` or `[PKG-001] 3-Ply Corrugated Box`) without leaving trailing empty brackets `()` or stripping dimensions halfway (e.g., `48mm x `).
- Format markdown line item table with aligned columns:
  - `#`
  - `Description` (including SKU code prefix)
  - `Quantity` (Quantity + UOM)
  - `Dimensions` (Length x Width x Height Unit or Length x Width Unit)
  - `Material / Specs` (Category/Material with Baseline: ₹X.XX)

---

## Phase 4: Testing, Verification & Regression Suite
- Run comprehensive unit and integration tests for:
  - Tabular BOQ intake with 30 items.
  - Document header and footer metadata isolation.
  - No false-positive modification intent on "settlement".
  - Multi-item modifications, additions, and deletions.
- Save execution logs to `artifacts/logs/test_tabular_intake.log` and `artifacts/logs/test_full_suite.log`.
- Update `README.md`, `architecture.md`, and `AI_context.md`.
