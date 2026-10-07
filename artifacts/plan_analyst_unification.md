# Implementation Plan: Decision Analyst & Vendor Response Unification

**Task Goal:** Combine Analyst Chat and Vendor Response skills into a unified workflow, eliminate the broken make.com webhook returning "Accepted", implement per-RFX graph/report persistence and token caching, add explanatory tab tooltips, hide the Award Scenario tab in the UI, and embed standard SQL-powered comparison widgets into the Comparison Grid without AI/token dependencies.

---

## 1. Objectives & Scope
1. **Unify Skills & Eliminate Broken Webhook**:
   - Merge "Check Vendor Response" and "Analyst Chat" into a unified entry point in `chatbot/index.html`.
   - Remove `vendorWebhookUrl` and `isVendorFlow` webhook routing returning "Accepted".
   - Clean `chatbot/config.json`.
2. **Artifact & Graph Caching per RFX ID**:
   - Store and cache generated charts, tables, and reports for each `rfx_id` in `AnalystRepository`.
   - Implement `(rfx_id, normalized_query)` cache lookup in `DecisionAnalystOrchestrator` to skip redundant LLM invocations and achieve 0-token instant reuse.
   - Expose `GET /api/rfx/{id}/artifacts` endpoint in `backend/app/api/analyst.py`.
   - Provide a "Saved Graphs & Reports" interface in `chatbot/analyst.html`.
3. **Tab Tooltips**:
   - Add Vuetify tooltips (`<v-tooltip>`) to each active tab in `chatbot/analyst.html` explaining its purpose and data contents.
4. **Hide Award Scenario Tab**:
   - Remove/hide the Award Scenario tab button and window item from `chatbot/analyst.html`.
5. **Ready-Made Comparison Widgets (Standard SQL)**:
   - Implement standard SQL calculations in `backend/app/analyst/tools/comparison.py`:
     - L1 Best Price Summary & Savings vs Target Price
     - Price Spread & Bidder Variance per Item
     - Vendor Win Count (L1 Leaderboard)
     - Optimal Basket vs Full Vendor Quoted Spend
   - Render these widgets in `chatbot/analyst.html` on top of the Comparison Grid.

---

## 2. Phased Implementation Steps
- [x] Phase 1: Planning & Architecture Review.
- [ ] Phase 2: Backend caching, graph/report persistence, and comparison widgets SQL in `backend/app/analyst/`.
- [ ] Phase 3: Expose `GET /api/rfx/{id}/artifacts` and update `GET /api/rfx/{id}/comparison`.
- [ ] Phase 4: Frontend updates to `chatbot/index.html`, `chatbot/config.json`, and `chatbot/analyst.html`.
- [ ] Phase 5: Automated testing, verification, and regression logs.
- [ ] Phase 6: Documentation sync (`README.md`, `architecture.md`, `AI_context.md`).
