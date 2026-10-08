# Implementation Plan: Clarify Decision-Support Scenarios & Disallow Premature Awarding

## Goal
Enforce that the AI Analyst and Chat screens serve exclusively as **decision-support and quotation evaluation tools**. The system must never execute binding awards or change RFX statuses to awarded, treating actual award execution as future scope.

---

## Phased Plan

### Phase 1: System Prompts & Orchestrator Decision Framing
- Update `config/prompts/analyst_system_v1.txt`:
  - Add explicit rule: The Analyst provides decision-support analysis, cost benchmarking, and scenario simulations.
  - Explicitly forbid claiming that items are officially awarded or changing RFX lifecycle status.
- Update `backend/app/analyst/orchestrator.py`:
  - Frame all optimization models as "Sourcing Scenario Evaluation" / "Simulation".
  - Include clear decision-support framing: "*(Note: This is a decision-support simulation. Actual line item awarding and status changes are future scope.)*".
  - Rename table headers from "Awarded Vendor" to "Suggested Vendor" / "Allocated Vendor (Scenario)".

### Phase 2: Verification with Automated Tests
- Run `PYTHONPATH=. .venv/bin/pytest tests/ backend/tests/ -v`.
- Verify benchmark evaluation questions and unit tests pass without regressions.
- Save execution trace to `artifacts/logs/test_decision_support_scenarios.log`.

### Phase 3: Documentation Parity
- Update `AI_context.md`, `architecture.md`, and `README.md` to document the decision-support scope boundary.
