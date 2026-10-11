# Implementation Plan: Fix .env Syntax Warnings and 404 HTTP Requests

## Problem Summary
1. **python-dotenv Warnings**:
   - `python-dotenv could not parse statement starting at line 15`
   - `python-dotenv could not parse statement starting at line 17`
   - **Root Cause**: Lines 16 and 18 of `.env` contain shell terminal commands (`.venv/bin/uvicorn ...` and `tmux new -s fastapi`) inadvertently appended to the file. `python-dotenv` expects valid `KEY=VALUE` pairs or comments.
2. **Frontend 404 Errors**:
   - `GET /ui/.env 404 Not Found`
   - `GET /.env 404 Not Found`
   - **Root Cause**: Legacy code in `chatbot/index.html` (lines 2500–2527) executes client-side `fetch('./.env')` and `fetch('../.env')` trying to discover fallback auth keys over HTTP. The server returns 404 as static static assets do not expose private `.env` files.

## Proposed Fixes
1. **Clean `.env` File**:
   - Remove lines 15–18 (the stray bash commands) from `.env`.
   - Ensure only valid environment configuration variables remain.
2. **Remove Unsafe Client-Side `.env` Fetch in `chatbot/index.html`**:
   - Remove the `fetch('./.env')` and `fetch('../.env')` fallback loop from `loadConfigFile()` in `chatbot/index.html`.
   - Client configuration is already properly loaded from `/chatbot/config.json` and fallback defaults (`chatbotStore.apiKey = 'aerchain3'`, `url = '/api/chat'`).
   - Eliminates the 404 HTTP requests on every UI load and prevents exposing `.env` lookups.

## Verification
- Test loading `.env` with `python-dotenv` without parse warnings.
- Run test suite to verify backend startup and API functionality.
- Save logs to `artifacts/logs/`.
