# Implementation Plan: Cloud Hosting Compatibility & Dynamic Endpoint Resolution

## Problem Statement
When hosted on Oracle Cloud (or any remote server/VPS), frontend users report that all major APIs fail and the site continues attempting to contact `http://localhost:8000/api/chat`.
- **Root Cause**:
  1. `chatbot/config.json` hardcodes `"url": "http://localhost:8000/api/chat"`.
  2. `chatbot/index.html` hardcodes `url: "http://localhost:8000/api/chat"` in Pinia `useChatbotStore` initial state.
  3. `chatbot/index.html` `loadConfigFile()` hardcodes fallback `chatbotStore.url = 'http://localhost:8000/api/chat'`.
  4. Pinia persists `chatbotStore` to `localStorage`. When users access the hosted site, any previously stored `localhost:8000` URL persists in the client's browser, directing API calls to the client's local machine rather than the Oracle Cloud host.
  5. In `comparison.py` and `trust.py`, database connection errors raise unhandled exceptions resulting in HTTP 500 errors if PostgreSQL on the cloud host is unreachable or misconfigured.

## Objectives
1. Make all frontend API calls host-agnostic using relative URLs (`/api/chat`, `/api/rfi`, etc.) and dynamic runtime fallback to `window.location.origin`.
2. Implement auto-healing in `chatbot/index.html` that detects cached `localhost` or `127.0.0.1` URLs in `localStorage` on non-localhost hosts and migrates them automatically to `/api/chat`.
3. Add robust exception handling in backend analyst tools (`comparison.py`, `trust.py`) and API routes so that database connection failures fail gracefully with clear diagnostics instead of unhandled 500 crashes.
4. Provide production runner support (`scripts/run_server.sh`) respecting `HOST` and `PORT` environment variables with `0.0.0.0` default.
5. Update `README.md`, `architecture.md`, and `AI_context.md` with cloud deployment guides and architectural documentation.

## Phased Execution

### Phase 1: Frontend Host Independence & Self-Healing
- Update `chatbot/config.json`: Set `"url": "/api/chat"`.
- Update `chatbot/index.html`:
  - Set default `chatbotStore.url = "/api/chat"`.
  - Add `/api/chat` to `defaultChoiceStore.url`.
  - In `loadConfigFile()`, inspect `chatbotStore.url`: if unset, pointing to `api2.aiql.com`, or pointing to `localhost:8000`/`127.0.0.1:8000`, auto-migrate to `/api/chat`.
  - In `createCompletion()`, add a runtime guard: if `targetUrl` contains `localhost:8000` or `127.0.0.1:8000` when accessed remotely, rewrite dynamically to `${window.location.origin}/api/chat`.
  - Ensure `isWebhook` check includes `/api/chat` so chat requests always send the FastAPI-compatible `{ message, conversation_id }` payload.

### Phase 2: Backend Resilience for Cloud Environments
- In `backend/app/analyst/tools/comparison.py`:
  - Wrap database connection and query execution in `try...except` to catch `psycopg.Error` or connection failures, returning structured error messages.
- In `backend/app/analyst/tools/trust.py`:
  - Catch database operational errors and return a clean `TrustReportResult` with low confidence and clear error diagnosis.
- In `backend/app/api/analyst.py`:
  - Enhance `/api/rfx/{id}/comparison` and `/api/rfx/{id}/trust` with clean error handling and informative HTTP error responses.
- In `scripts/demo_submit.sh`:
  - Support `BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"`.

### Phase 3: Production Server Script & Deployment Helpers
- Add `scripts/run_server.sh`:
  - Configurable `HOST="${HOST:-0.0.0.0}"` and `PORT="${PORT:-8000}"`.
  - Launch uvicorn with appropriate workers and settings.
- Verify CORS and reverse proxy handling in `backend/app/main.py`.

### Phase 4: Verification & Test Execution
- Run unit and integration tests across the test suite.
- Save execution logs in `artifacts/logs/test_hosting_compatibility.log`.
- Test static file resolution and API routing.

### Phase 5: Documentation Synchronization
- Update `README.md` with Oracle Cloud hosting instructions, port configuration, and troubleshooting.
- Update `architecture.md` with host-agnostic frontend communication details.
- Update `AI_context.md` with Step 13 Hosting Compatibility notes.
