#!/usr/bin/env bash
# Start the Heard API server (dev mode, auto-reload).
#
#   ./run.sh              server only
#   ./run.sh --tunnel     server + HTTPS tunnel, needed to test on an iPhone
#                         (Safari only grants the microphone on https origins)
#   ./run.sh --photon     server + the Photon iMessage relay
#   ./run.sh --all        server + tunnel + relay
#
# TUNNEL=ngrok selects ngrok instead of cloudflared. See docs/https-tunnel.md.
set -euo pipefail
cd "$(dirname "$0")"

WITH_TUNNEL=0
WITH_PHOTON=0
for arg in "$@"; do
  case "$arg" in
    --tunnel) WITH_TUNNEL=1 ;;
    --photon) WITH_PHOTON=1 ;;
    --all) WITH_TUNNEL=1; WITH_PHOTON=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

PORT="${PORT:-8000}"
PIDS=()
# Kill the whole group: cloudflared and uvicorn --reload both spawn children.
cleanup() { for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if [ -x .venv/bin/uvicorn ]; then UVICORN=.venv/bin/uvicorn; else UVICORN=uvicorn; fi

if [ "$WITH_PHOTON" = 1 ]; then
  if command -v node >/dev/null 2>&1; then
    echo "[run] starting Photon relay on :${PHOTON_PORT:-3001}"
    PORT="${PHOTON_PORT:-3001}" node photon-service/src/index.ts &
    PIDS+=($!)
  else
    echo "[run] node not found; skipping the Photon relay" >&2
  fi
fi

if [ "$WITH_TUNNEL" = 1 ]; then
  case "${TUNNEL:-cloudflared}" in
    cloudflared)
      if command -v cloudflared >/dev/null 2>&1; then
        echo "[run] starting cloudflared tunnel -> http://localhost:$PORT"
        cloudflared tunnel --url "http://localhost:$PORT" &
        PIDS+=($!)
      else
        echo "[run] cloudflared not found. brew install cloudflared" >&2
        exit 1
      fi
      ;;
    ngrok)
      if command -v ngrok >/dev/null 2>&1; then
        echo "[run] starting ngrok -> http://localhost:$PORT"
        ngrok http "$PORT" &
        PIDS+=($!)
      else
        echo "[run] ngrok not found. brew install ngrok" >&2
        exit 1
      fi
      ;;
    *) echo "[run] unknown TUNNEL=${TUNNEL}; use cloudflared or ngrok" >&2; exit 2 ;;
  esac
fi

# Exec would discard the trap, so run in the foreground and wait instead.
"$UVICORN" app.main:app --reload --host 0.0.0.0 --port "$PORT" &
PIDS+=($!)
wait -n
