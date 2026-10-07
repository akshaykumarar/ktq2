# Implementation Plan - Step 3: Natural Language RFx Analysis & Decision Support

## Overview
Step 3 equips buyers with natural language analysis, side-by-side comparisons, deterministic award optimization, trust/risk profiling, Chart.js visual analytics, and export packs (XLSX) with complete auditability, guardrails, and observability.

---

## Phased Execution Checkpoints

### CP1: Wren Inspection, Semantic Layer & Text-to-SQL Fallback
- **Repo & Wren Status Analysis**:
  - `WrenAI-main` contains Rust `wren-core`, `wren-core-py` (PyO3 bindings), `wren` Python engine, and `wren-pydantic`.
  - In Python 3.14 (macOS ARM64), `wren_core` native Rust extensions are not prebuilt and `pyarrow`/`sqlglot` are not in the main environment.
  - Provide a semantic model manifest (`config/wren_semantic_model.yaml` & `config/wren_mdl.json`) with rich business descriptions and business rules for all Step 2 views and entities.
  - Implement the semantic text-to-SQL fallback service (`backend/app/analyst/semantic.py`) using schema-aware prompt engineering, few-shot retrieval from `config/wren_examples.yaml` (20+ seeded question-to-SQL pairs), strict read-only AST/regex validation, and single-retry error feedback.
  - Provide model deployment and example reload utilities (`scripts/deploy_wren_model.py`, `scripts/reload_wren_examples.py`).
  - Verify 10 basic questions return accurate SQL and execution rows against live PostgreSQL data.

### CP2: Database Migration & Core Decision Tools
- **Database Migration (`migrations/003_analyst.sql`)**:
  - `ktq.analyst_traces`: Detailed telemetry of each analyst interaction (question, tools called, SQL executed, path used, latencies, tokens, confidence, full JSON response).
  - `ktq.analyst_feedback`: User ratings, comments, corrected SQL, and corrected answers linked to traces.
  - `ktq.award_scenarios`: Stored allocation scenarios with constraints, overrides, and finalization status.
  - `ktq.audit_log`: Audit trail recording finalized award scenarios and accepted REVIEW flags.
- **Backend Repositories & Tools (`backend/app/analyst/`)**:
  - `tools/sql_runner.py` (`run_sql`): Read-only SELECT validator enforcing allowed views, statement timeouts, and LIMIT clauses.
  - `tools/trust.py` (`trust_report`): Calculates vendor/item reliability, coverage %, CONFIDENT vs REVIEW vs MISSING breakdown, critical flags, expired documents, and money at risk.
  - `tools/comparison.py` (`get_comparison_grid`): Side-by-side grid matrix (items x vendors: price, state, flags, crops, coverage %, totals, questionnaire status).
  - `tools/assumptions.py` (`get_assumptions`, `set_assumption`): Session-scoped what-if parameters (FX rates, freight rules, GST treatment, outlier thresholds).
  - Unit tests for tools with execution output logged to `artifacts/logs/test_cp2_tools.log`.

### CP3: Award Optimizer Engine
- **Deterministic Python Solver (`backend/app/analyst/optimizer.py`)**:
  - Solves multi-criteria RFx allocations without LLM calculation:
    - Cheapest-per-line (split award).
    - Single vendor award vs multi-vendor split.
    - Quality gate filtering: require vendors to pass all knockout questions (distinguishing `fail` from `unanswered`/`needs_review` unresolved states).
    - Maximum allocation constraint per vendor (e.g., max 60% of total spend/volume).
    - Inclusion/exclusion of REVIEW-state prices.
    - What-if overrides (freight per kg/trip, foreign exchange shifts, custom vendor price revisions).
    - Savings calculations vs baseline / prior year and L1 spread.
  - Returns complete line-by-line allocation, vendor totals, excluded vendors with explicit reasoning, and risk caveats.
  - Unit tests covering edge cases in `tests/test_award_optimizer.py` logged to `artifacts/logs/test_cp3_optimizer.log`.

### CP4: Orchestrator Agent & `/api/analyst/ask`
- **Orchestrator Architecture (`backend/app/analyst/orchestrator.py`)**:
  - Tool-use workflow coordinating `ask_data`, `run_sql`, `award_optimizer`, `trust_report`, `make_chart`, `export_file`, and assumptions.
  - System prompt loaded from versioned `config/prompts/analyst_system_v1.txt`.
  - Comparability guardrails: always verify and state coverage before comparing vendor totals; flag like-for-like subsets.
  - State transparency: report all REVIEW or MISSING values influencing conclusions.
  - Number traceability post-check: regex-based scanner verifying that all numbers stated in `answer_text` originate from deterministic tool outputs or math derivatives; downgrades confidence or regenerates if ungrounded.
  - Full standardized response contract:
    `{answer_text, tables, charts, exports, caveats, how_i_got_this, confidence, confidence_reason, suggested_followups, trace_id}`.
  - REST endpoints in `backend/app/api/analyst.py`:
    - `POST /api/analyst/ask`
    - `GET /api/analyst/sessions/{id}`
    - `POST /api/analyst/feedback`
    - `GET /api/rfx/{id}/comparison`
    - `GET /api/rfx/{id}/trust`

### CP5: Visualizations, Excel Exports & Scenario Finalization
- **Chart Generation (`backend/app/analyst/charts.py`)**:
  - Deterministic Chart.js spec generator (bar, grouped bar, line, scatter, stacked) using actual tool row data.
- **Export Engine (`backend/app/analyst/exports.py` & `GET /api/exports/{id}`)**:
  - Multi-tab Excel workbook generation via `openpyxl`:
    - Summary & Grid sheet.
    - Award Pack allocation sheet.
    - Dedicated "Assumptions and Caveats" sheet detailing FX rates, freight/tax rules, REVIEW items utilized, and exclusion rationales.
- **Scenario Management & Finalization**:
  - `POST /api/rfx/{id}/award/scenarios`: Create / run scenario.
  - `GET /api/rfx/{id}/award/scenarios/{sid}`: Retrieve scenario.
  - `POST /api/rfx/{id}/award/scenarios/{sid}/finalize`: Validates if unresolved REVIEW items affect awarded items; blocks finalization unless buyer explicitly submits accepted flags; logs to `ktq.audit_log`.

### CP6: Minimal Working Frontend Tabs
- **Client-Side Tabs in `chatbot/index.html`**:
  1. **Comparison Grid Tab**: Items as rows, vendors as columns, state color-coded badges, coverage summary row, modal popover on cell click showing raw vs normalized price, UOM conversion, flags, reasoning, and visual bounding crop.
  2. **Trust & Risk Tab**: Vendor trust scores, coverage %, price reliability (CONFIDENT / REVIEW / MISSING), critical flags table, and money-at-risk ranking.
  3. **Analyst Chat Tab**: Conversation history, Markdown narration, interactive tables, Chart.js rendered charts, download cards, collapsible "How I Got This" drawer (tool steps, executed SQL, included/excluded vendors), and thumbs up/down rating with SQL correction input.
  4. **Award Scenarios Tab**: Interactive scenario builder (split vs single, max share %, knockout filter, what-if inputs), visual allocation breakdown, and finalize flow with flag acceptance modal.

### CP7: Evaluation Benchmark, Tuning Guide & Documentation
- **Evaluation Suite (`scripts/eval_analyst.py` & `config/eval_questions.yaml`)**:
  - 30+ comprehensive test questions (ranking, line splits, knockout filters, chart generation, trust questions, what-if calculations, adversarial/ambiguous queries).
  - Ground truth comparison using independent pandas/SQL queries.
  - Evaluates number correctness, coverage caveats, flagged item disclosures, and absence of hallucinated figures.
  - Generates summary pass/fail matrix and logs to `artifacts/logs/eval_analyst.log`.
- **Feedback Exporter (`scripts/export_feedback_to_examples.py`)**:
  - Extracts corrected SQL and answers from `ktq.analyst_feedback` into `config/wren_examples.yaml`.
- **Documentation & Tuning Guide**:
  - Update `README.md` with complete endpoint reference and curl examples.
  - Update `architecture.md` with Step 3 analytical data flows.
  - Update `AI_context.md` with current state and components.
  - Tuning guide in `README.md` ranking knobs (semantic descriptions, examples, prompt templates, optimizer heuristics, confidence thresholds).
