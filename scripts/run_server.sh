#!/usr/bin/env bash
# Production & Cloud Hosting Server Startup Script
set -e

# Default host to 0.0.0.0 to listen on all interfaces (required for cloud hosting)
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"

# Determine Python binary
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
    UVICORN=".venv/bin/uvicorn"
elif command -v uvicorn >/dev/null 2>&1; then
    PYTHON="python3"
    UVICORN="uvicorn"
else
    PYTHON="python3"
    UVICORN="python3 -m uvicorn"
fi

echo "================================================================================"
echo "Starting AI-QL Procurement Backend"
echo "Host: ${HOST}"
echo "Port: ${PORT}"
echo "Workers: ${WORKERS}"
echo "Environment: ${ENV:-production}"
echo "================================================================================"

exec $UVICORN backend.app.main:app \
    --host "${HOST}" \
    --port "${PORT}" \
    --proxy-headers \
    --forwarded-allow-ips "*"
