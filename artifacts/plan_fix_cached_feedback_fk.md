# Implementation Plan: Fix Analyst Feedback Foreign Key Constraint for Cached Responses

## Issue Summary
When the Decision Analyst returns a cached response, it assigns a synthetic trace ID formatted as `cached_{trace_id}` (e.g., `cached_3ecb8532-6de`). However, cached responses did not persist a record to `ktq.analyst_traces`. When the client subsequently calls `POST /api/analyst/feedback`, PostgreSQL raises `ForeignKeyViolation` on `analyst_feedback_trace_id_fkey`, returning HTTP 500 Internal Server Error.

## Root Cause
1. `backend/app/analyst/orchestrator.py`: In `ask()`, cache hits returned early after modifying `resp_model.trace_id = f"cached_{trace_id}"` without calling `self._repo.record_trace()`.
2. `backend/app/analyst/repository.py`: In `record_feedback()`, the query directly executed `INSERT INTO ktq.analyst_feedback` without ensuring the referenced `trace_id` existed in `ktq.analyst_traces` or handling foreign key violations gracefully.

## Phased Implementation Plan

### Phase 1: Orchestrator Cache Trace Persistence
- Update `backend/app/analyst/orchestrator.py`:
  - When returning a cached response in `ask()`, invoke `self._repo.record_trace(...)` with:
    - `trace_id = cached_trace_id`
    - `path_used = "cache"`
    - `tokens = {"prompt_tokens": 0, "completion_tokens": 0}`
    - `final_response = resp_dict`
    - Preserved question, session ID, tool steps, and SQL from the cached response.
  - Ensures trace telemetry, session history (`get_session_history`), and foreign key relationships are maintained.

### Phase 2: Repository Feedback Resilience (Defense-in-Depth)
- Update `backend/app/analyst/repository.py`:
  - In `record_feedback()`:
    - Verify whether `feedback.trace_id` exists in `ktq.analyst_traces`.
    - If missing (e.g., for legacy cached traces already present in user browser sessions or orphaned IDs), auto-insert a stub trace record in `ktq.analyst_traces` (`ON CONFLICT (trace_id) DO NOTHING`).
    - If `psycopg.errors.ForeignKeyViolation` is raised, fallback gracefully to storing the feedback with `trace_id = None` rather than failing with 500.

### Phase 3: Verification & Test Coverage
- Add unit/mock tests in `tests/test_analyst_feedback_cache.py`:
  - Test feedback submission with cached trace ID.
  - Test feedback submission with orphaned trace ID triggering stub trace creation.
  - Test graceful fallback if foreign key constraint fails.
- Run tests and save test log to `artifacts/logs/test_analyst_feedback_fix.log`.

### Phase 4: Documentation Updates
- Update `AI_context.md`, `architecture.md`, and `README.md`.
