# Phased Implementation Plan: Comparison Grid Fix & Vendor Comparison Redesign

## Task Overview
1. **Fix Comparison Grid Tables Visibility & Reliability**:
   - Ensure all comparison tables (Side-by-Side Matrix, L1 Lowest Price Summary, Line Item Price Spread & Variance, Vendor Win Leaderboard, Vendor Coverage Breakdown) are fully visible, robustly rendered, and connected to PostgreSQL database data.
   - Add dedicated loading states (`loadingGrid`), error alerts (`gridError`), and empty state messaging when an RFX has no quotations.
   - Fix client-side template crashes caused by unsafe `.toLocaleString()` on undefined/null values by introducing standardized formatting helper methods (`formatCurrency`, `formatNumber`).
   - Enhance the table designs with clear styling, tooltips, evidence inspection, and sticky headers.

2. **Redesign Trust & Risk Tab into "Vendor Comparison" Tab**:
   - Rename tab from "Trust & Risk" to "Vendor Comparison" across tabs, tooltips, and header banners.
   - Convert disparate vendor cards into a comprehensive, single-line-item-per-vendor comparison table.
   - Add explicit score reasoning (`score_reasoning`), risk level (`risk_level`), and total spend (`total_quoted_spend_inr`) in `VendorTrustSummary` domain model and computation tool (`compute_trust_report`).
   - Display key procurement decision metrics per vendor row:
     - Vendor Name & Coverage Status
     - Trust Score & Risk Level badge (Low / Medium / High Risk)
     - Score Reasoning & Points Breakdown (Coverage, Price Quality, Compliance, Penalties)
     - Item Coverage (Quoted count / Total items, Coverage %)
     - Price Confidence Breakdown (Confident vs Review vs Missing)
     - Knockout & Compliance Status (Passed / Unresolved / Failed / N/A)
     - Open Flags & Warnings (Critical & warning counts with tooltip details)
     - Money at Risk (INR amount tied to review lines)
     - Total Quoted Spend (INR)
   - Include quick keyword search/filter across vendors and summary KPI header banner.

---

## Phases of Implementation

### Phase 1: Backend Domain Models & Trust Tool Enhancement
- Update `backend/app/analyst/models.py`:
  - Add `score_reasoning: str`, `risk_level: str`, and `total_quoted_spend_inr: float = 0.0` to `VendorTrustSummary`.
- Update `backend/app/analyst/tools/trust.py`:
  - Compute transparent, deterministic `score_reasoning` explaining base points (+40 max for coverage, +30 max for price confidence, +10 max for knockouts) and deductions (-10 per critical flag, -3 per warning).
  - Calculate `risk_level` ("Low Risk" for trust >= 80, "Moderate Risk" for 50-79, "High Risk" for < 50).
  - Include total quoted spend per vendor from comparison rows.
  - Ensure type hints and docstrings on all functions.

### Phase 2: Frontend Comparison Grid & Table Fixes (`chatbot/analyst.html`)
- Implement `loadingGrid` and `gridError` reactive states in Vue setup.
- Add robust null-safe formatting helpers (`formatCurrency`, `formatNumber`, `safeLocaleString`).
- Enhance all 5 view modes (Matrix, L1 Best Price, Price Spread, Vendor Coverage, Win Leaderboard) with complete table structures, badges, and empty states.
- Ensure smooth view switching via button toggles with active indicators.

### Phase 3: Frontend Vendor Comparison Redesign (`chatbot/analyst.html`)
- Rename Tab 3 from "Trust & Risk" to "Vendor Comparison" (`<v-tab value="trust">` with label "Vendor Comparison").
- Replace card grid with a unified data table where each vendor is exactly 1 row.
- Render all decision criteria: Trust Score, Risk Tier, Score Reasoning, Item Coverage %, Confident/Review counts, Compliance status, Critical flags, Money at Risk, and Total Spend.
- Add vendor search filter and summary KPI banner.

### Phase 4: Verification & Test Execution
- Run unit test suite: `PYTHONPATH=. .venv/bin/pytest tests/ backend/tests/ -v`.
- Save test execution log to `artifacts/logs/test_grid_and_vendor_comparison.log`.
- Verify database queries, mock fallbacks, and endpoint contracts.

### Phase 5: Documentation Updates
- Update `README.md`, `architecture.md`, and `AI_context.md` with the new Vendor Comparison design, reasoning fields, and grid fixes.
