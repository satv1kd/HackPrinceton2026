#!/usr/bin/env bash
# Install the system tools Heard needs - only the ones that are missing.
#
# Safe to run again and again: it never upgrades anything. That matters
# because upgrading Homebrew's python@3.13 deletes the exact Python build that
# .venv points at, which breaks the virtualenv (make install then rebuilds it).
#
#   ./scripts/setup.sh            python 3.13, ffmpeg, node, cloudflared
#   SKIP_CLOUDFLARED=1 ./scripts/setup.sh   laptop-only use, no phone tunnel
set -euo pipefail

if ! command -v brew >/dev/null 2>&1; then
  echo "[setup] Homebrew is required: https://brew.sh" >&2
  exit 1
fi

# Never upgrade already-installed formulae, and don't spend a minute
# refreshing Homebrew's index on every run.
export HOMEBREW_NO_INSTALL_UPGRADE=1
export HOMEBREW_NO_AUTO_UPDATE=1

need() {  # need <command> <formula>
  if command -v "$1" >/dev/null 2>&1; then
    echo "[setup] $1: already available ($(command -v "$1"))"
  else
    echo "[setup] $1: installing $2"
    brew install "$2"
  fi
}

need python3.13 python@3.13
need ffmpeg ffmpeg
# Any Node 22.6+ works (Homebrew's or not); only install if there is none.
need node node
if [ "${SKIP_CLOUDFLARED:-0}" != 1 ]; then
  need cloudflared cloudflared
fi

node_major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
if [ "$node_major" -lt 22 ]; then
  echo "[setup] warning: Node $(node --version 2>/dev/null) is older than 22.6; the Photon relay needs 22.6+" >&2
fi
echo "[setup] done. Next: make install"
