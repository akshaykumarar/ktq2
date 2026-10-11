# Phased Implementation Plan: Fine-Tuning Core Capabilities & Whole-Picture Documentation

## Overview
This plan implements the 5 fine-tuning enhancements across decision analyst prompting, semantic SQL generation, multi-tier volumetric extraction, MOQ award optimization, and SQL AST security parser, followed by a complete consolidation of `README.md`, `architecture.md`, and `AI_context.md` into clean, unified, whole-picture documents.

---

## Phase 1: Model Prompting & Nuanced Multi-Vendor Trade-Offs
- **Target File**: `config/prompts/analyst_system_v1.txt`
- **Objective**: Refine prompt guidance so the AI Decision Analyst provides nuanced trade-off summaries when evaluating multi-vendor splits:
  - Total Spend vs L1 Delta (quantify cost premium for redundancy).
  - Supplier Concentration Risk Reduction (eliminate single point of failure).
  - Operational & Relationship Overhead (managing N vendors vs 1, MOQ thresholds, split logistics).
  - Item-Level Lead Time & Quality Trade-Offs.

## Phase 2: SQL Generation & Multi-Table Joins
- **Target File**: `config/wren_examples.yaml`
- **Objective**: Add rich multi-table join examples:
  - Cross-referencing knockout questionnaire answers, vendor qualifications, and item prices.
  - Cross-referencing commercial payment/delivery terms with line item spend.
  - Cross-referencing open critical review flags with line item savings.

## Phase 3: Extraction Tuning for Multi-Tier Volumetric Tables
- **Target Files**: `backend/app/vendor/prompts.py`, `backend/app/vendor/extract.py`
- **Objective**:
  - Enhance `SYSTEM_EXTRACTION_PROMPT` and `USER_EXTRACTION_PROMPT_TEMPLATE` with precise guidance for volumetric discount tables in OCR scans (quantity brackets, volume tiers, MOQ rate breaks, misaligned OCR columns).
  - Enhance `deterministic_text_extractor` in `extract.py` to parse volumetric brackets (e.g. `100-500 @ ₹45`, `501-2000 @ ₹40`) into structured line items and discount metadata.

## Phase 4: Optimizer Scaling with MOQ Penalty Constraints
- **Target Files**: `backend/app/analyst/models.py`, `backend/app/analyst/optimizer.py`
- **Objective**:
  - Add MOQ constraint options to `AwardConstraints` (`enforce_moq`, `moq_penalty_mode`, `moq_shortfall_penalty_rate`).
  - Update `LineAllocation` to capture `moq`, `moq_penalty_inr`, and `effective_order_quantity`.
  - Fetch `ri.moq` from `ktq.response_items` in `optimize_award`.
  - Implement deterministic MOQ penalty modes (`shortfall_penalty`, `quantity_bump`, `exclude_below_moq`) with transparent caveats and totals.

## Phase 5: SQL AST Security via `sqlglot`
- **Target Files**: `backend/app/analyst/tools/sql_runner.py`, `requirements.txt`
- **Objective**:
  - Integrate `sqlglot` to parse queries into an AST.
  - Extract all referenced tables across main query, CTEs, subqueries, and JOINs.
  - Strictly enforce allowlist against all extracted tables; reject queries accessing unauthorized tables (e.g. `information_schema`, `ktq.rfx_activity`, etc.).
  - Distinguish internal CTE aliases from physical database tables.

## Phase 6: Test Suite & Traceability
- **Target Files**: `tests/test_analyst_tools.py`, `tests/test_award_optimizer.py`, `tests/test_vendor_intake.py`
- **Objective**:
  - Add unit tests for AST parser with nested subqueries, CTEs, and allowlist enforcement.
  - Add unit tests for MOQ penalty allocation calculations.
  - Add unit tests for volumetric discount extraction.
  - Run the full test suite and save logs to `artifacts/logs/test_fine_tuning_and_consolidation.log`.

## Phase 7: Holistic Documentation Cleanup (Whole-Picture Architecture & README)
- **Target Files**: `README.md`, `architecture.md`, `AI_context.md`
- **Objective**:
  - Remove all fragmented step-by-step change logs ("Step 1...", "Step 2...", "Step 11...", etc.).
  - Re-author each document into a cohesive, comprehensive, whole-picture representation of the entire product.
