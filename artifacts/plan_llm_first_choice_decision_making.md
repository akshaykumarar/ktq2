# Implementation Plan: LLM as First Choice for Decisions and Orchestration

## 1. Goal & Architecture Intent
Ensure that the LLM (Large Language Model) via PydanticAI agents is the **primary / first-choice engine** for:
1. Multi-agent routing, intent understanding, and conversational decision making.
2. Requirement extraction and line item structuring (`rfi_parser` agent).
3. Natural language terms and item modification interpretation.
4. Specialist task delegation (`rfx`, `vendor`, `status`).

Deterministic algorithms and rule-based heuristics serve strictly as high-reliability fallbacks when offline or in test environments.

## 2. Changes & Phasing

### Phase 1: LLM Agent Injection in Orchestration (`backend/app/agents/master.py`)
- Pass `specialists` into `handle_rfi_workflow_turn()` to provide the `rfi_parser` LLM agent.
- Use `rfi_parser` agent as first choice in `extract_requirements(user_message, agent=parser_agent)`.
- When live LLMs are configured (not `TestModel`), prioritize LLM orchestration in `run_master_orchestration()`.

### Phase 2: Enhanced PydanticAI Parser Prompting (`backend/app/intake/agent.py`)
- Update `DEFAULT_PARSER_INSTRUCTIONS` with explicit instructions for key-value row parsing, baseline price extraction, 2D/3D dimensions, and solicitation preamble exclusion.

### Phase 3: Verification & Test Traceability
- Verify full test suite passes with mock/live modes.
- Save execution log to `artifacts/logs/test_full_suite.log`.
- Update `README.md`, `architecture.md`, and `AI_context.md`.
