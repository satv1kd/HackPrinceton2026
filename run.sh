#!/usr/bin/env bash
# Start the Heard API server (dev mode, auto-reload).
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/uvicorn ]; then UVICORN=.venv/bin/uvicorn; else UVICORN=uvicorn; fi
exec "$UVICORN" app.main:app --reload --host 0.0.0.0 --port "${PORT:-8000}"
