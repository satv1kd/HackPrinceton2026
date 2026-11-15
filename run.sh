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

# Refuse to start on a busy port, and say who has it. Without this, a second
# `make dev-all` (often a forgotten one in another terminal tab) crashes with
# "Address already in use" from uvicorn and a Node stack trace from the relay.
port_owner() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1; }
short_cmd() {  # the command line, with Homebrew's long Python path shortened
  ps -o command= -p "$1" 2>/dev/null | sed -E 's#^/[^ ]*/(Python|python3[.0-9]*)( |$)#python\2#' | cut -c1-70
}
OLD_RUN=""
check_port() {  # check_port <port> <what>
  local pid parent
  pid=$(port_owner "$1") || true
  [ -z "$pid" ] && return 0
  echo "[run] port $1 ($2) is in use by pid $pid: $(short_cmd "$pid")" >&2
  # Was it started by another run.sh? Killing that one stops everything it started.
  parent=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
  if [ -n "$parent" ] && ps -o command= -p "$parent" 2>/dev/null | grep -q 'run\.sh'; then OLD_RUN=$parent; fi
  return 1
}
busy=0
check_port "$PORT" "server" || busy=1
if [ "$WITH_PHOTON" = 1 ]; then check_port "${PHOTON_PORT:-3001}" "iMessage relay" || busy=1; fi
if [ "$busy" = 1 ]; then
  echo "[run] Heard is probably already running, maybe in another terminal tab." >&2
  echo "[run] Use that one (http://localhost:$PORT), or stop it with Ctrl+C in its tab" >&2
  if [ -n "$OLD_RUN" ]; then
    echo "[run] or run:  kill $OLD_RUN   (the earlier ./run.sh; stops its server, relay and tunnel)" >&2
  else
    echo "[run] or stop the process above, then start again." >&2
  fi
  echo "[run] To run a second copy instead: PORT=8001 PHOTON_PORT=3002 ./run.sh" >&2
  exit 1
fi

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
SERVER_PID=$!
PIDS+=("$SERVER_PID")
# Plain `wait <pid>`, not `wait -n`: macOS ships bash 3.2, which lacks -n.
# The server is the process that matters; Ctrl+C triggers the cleanup trap.
wait "$SERVER_PID"
