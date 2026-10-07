# Implementation Plan: Add RFI Specification Table Tab to Analyst Dashboard

## Overview
Add a dedicated tab in the AI Procurement Decision Analyst dashboard (`chatbot/analyst.html`) that fetches and displays the complete RFI details and line items table for the currently selected RFX ID (`rfxId`).

---

## Proposed Changes

### 1. Tab & View Template (`chatbot/analyst.html`)
- Add `<v-tab value="rfi">` with icon `mdi-file-document-outline` and tooltip.
- Add `<v-window-item value="rfi">` containing:
  - **Overview KPI Cards**:
    - RFX Title, Category, Scope, and Status chip.
    - Commercial terms: Payment Terms, Delivery Terms, Validity Days, Response Deadline, Currency.
    - Summary metrics: Total Line Items and Total Target Spend.
  - **Line Items Data Table**:
    - `#` (Item number)
    - `Description`
    - `Quantity & Unit`
    - `Material`
    - `Dimensions (L x W x H)`
    - `Target Price (INR)`
    - `Total Target Value (INR)`
    - `Required Date`
    - `Specifications / Notes`
  - **Loading & Error Handling**:
    - Progress circular loader during fetch.
    - Error / Empty state alert if RFI details cannot be loaded.
    - Quick refresh button.

### 2. Vue State & Data Methods (`chatbot/analyst.html`)
- Reactive properties: `rfiData`, `loadingRfi`, `rfiError`.
- `loadRfiDetails(id)`: Fetches `GET /api/rfi/{id}` and populates `rfiData`.
- Helper functions:
  - `formatDimensions(dim)`: Nicely formats `{length, width, height, unit}` into `L x W x H unit` or string.
  - `calcTotalTargetSpend(items)`: Computes sum of `quantity * target_price`.
- Integration into lifecycle:
  - Trigger `loadRfiDetails()` inside `loadAllData()` and `onRfxChanged()`.

### 3. Verification & Testing
- Pytest suite regression verification.
- Verify frontend rendering and API response contract with mock and live models.
- Update documentation: `AI_context.md`, `architecture.md`, `README.md`.
