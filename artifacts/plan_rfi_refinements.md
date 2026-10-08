# Phased Implementation Plan: RFI Creation Refinements (Terms Filtering & Dynamic Item Operations)

## Overview & Objectives
1. **Terms & Conditions / Separators Exclusion from Line Items**:
   - Prevent horizontal separator lines (`--------------------`, `====`, `____`), commercial terms headings (`Mandatory Commercial Terms:`, `Commercial Terms To Include:`), and condition bullet points (MOQ, Freight/Shipping, Warranty/SLA, Volume Discounts, Payment Terms, Currency clauses) from being erroneously parsed as packaging line items with default quantities and materials.
   - Automatically extract recognized commercial terms into RFI metadata attributes (`payment_terms`, `delivery_terms`, `validity_days`, `currency`, `scope`/`commercial_terms_notes`) rather than cluttering line items.
   - Ensure clean filtering across LLM prompts, deterministic parser (`backend/app/intake/agent.py`), Excel parser (`backend/app/intake/excel_parser.py`), validation rules (`backend/app/intake/validation.py`), and Master agent orchestration (`backend/app/agents/master.py`).

2. **Flexible Natural Language Add, Remove, and Update Operations**:
   - Support removing items by:
     - **Range**: e.g., `remove items 33 to 39`, `remove 33-39`, `delete items from 33 to 39`, `remove lines 33 through 39`, `delete items between 33 and 39`.
     - **Multiple Items / List**: e.g., `remove items 33, 34, 35`, `delete line 1, 3, 5`, `remove item 1 and item 2`.
     - **Single Item**: e.g., `remove item 3`, `delete line 3`, `drop #3`.
     - **Relative Positions**: e.g., `remove last item`, `delete last 7 items`.
     - **Keywords / Description / Code**: e.g., `remove brown tape`, `delete [Pkg-029]`, `remove commercial terms items`, `delete separator line`.
     - **Natural Phrasing**: e.g., `please remove items 33 to 39 as they are commercial terms`.
   - Support adding items with explicit phrases (`add 500 rolls of brown tape`, `can you also add 200 boxes of 10x10x10`, `include 50kg stretch film`).
   - Support updating items (`change item 2 quantity to 500`, `update item 1 price to 25`).
   - Re-index remaining line items (`1..N`) atomically after any removal.

---

## Phase 1: Commercial Terms & Separators Detection & Extraction Engine
- **File**: `backend/app/intake/validation.py`
  - Add `COMMERCIAL_TERMS_PATTERNS`, `SEPARATOR_PATTERNS`, and `HEADER_PATTERNS`.
  - Add helper functions:
    - `is_separator_line(text: str) -> bool`
    - `is_commercial_term_or_header(text: str) -> bool`
    - `extract_commercial_terms(text: str) -> dict[str, Any]` (detects payment terms, freight/delivery terms, validity, warranty/SLA, MOQ notes, currency, custom conditions).
    - `filter_packaging_clauses(clauses: list[str]) -> tuple[list[str], dict[str, Any]]`

---

## Phase 2: Parser & Extractor Upgrades (Deterministic, Excel, LLM)
- **File**: `backend/app/intake/agent.py`
  - Update `DEFAULT_PARSER_INSTRUCTIONS` with explicit instructions to ignore separator lines and classify commercial terms as terms/metadata instead of line items.
  - In `deterministic_extract_packaging(text)`:
    - Pre-process text to separate commercial terms/notes and strip separator lines before clause splitting.
    - Filter out non-item clauses during parsing.
    - Populate RFI terms/defaults from extracted commercial terms.
- **File**: `backend/app/intake/excel_parser.py`
  - In `rows_to_packaging_requirements(rows)`:
    - Skip rows that match separator patterns, header patterns, or commercial terms.
    - Extract any embedded commercial terms into return metadata.

---

## Phase 3: Repository Multi-Item & Range Deletion & Item Update
- **File**: `backend/app/db/repository.py`
  - Enhance `remove_item` and add `remove_items(rfi_id: int, item_identifiers: list[int | str]) -> dict[str, Any] | None`.
  - Support deleting by range of item numbers, list of numbers, item codes, and keyword patterns in a single atomic SQL transaction (and memory store fallback).
  - Automatically re-index remaining line items to maintain contiguous `1..N` numbering.
  - Add `update_item(rfi_id: int, item_identifier: int | str, updates: dict[str, Any]) -> dict[str, Any] | None`.

---

## Phase 4: Conversational Master Agent Enhancements
- **File**: `backend/app/agents/master.py`
  - Upgrade natural language intent parsing in `handle_rfi_workflow_turn()`:
    - Detect multi-item removal, range removal (`33 to 39`, `33-39`, `from 33 to 39`), relative removal (`last 7 items`), and keyword removal (`commercial terms`, `separator`).
    - Detect item updates (`update item 2 quantity to 500`).
    - Detect item addition (`add 200 rolls of brown tape`).
    - Extract and merge commercial terms from user input into active RFI.
    - Display updated RFI summary card with clean line items and structured commercial terms.

---

## Phase 5: Verification, Testing & Documentation
- **Tests**:
  - Add unit and integration tests in `backend/tests/test_chat_rfi_workflow.py` and `backend/tests/test_text_intake.py` testing:
    1. Input with items + separator lines + commercial terms (like the screenshot): verifies items 33-39 are NOT created as line items, and terms are captured in commercial terms section.
    2. Range removal: `remove items 33 to 39`, `delete 33-39`, `remove items 5 to 7`.
    3. Multi-item removal: `remove items 2, 4, 6`.
    4. Relative removal: `remove last item`, `remove last 3 items`.
    5. Keyword removal: `remove brown tape`, `remove commercial terms`.
    6. Adding new items incrementally.
    7. Modifying item quantity.
- Run full pytest suite, save logs to `artifacts/logs/test_full_suite.log`.
- Update `README.md`, `architecture.md`, `AI_context.md`.
