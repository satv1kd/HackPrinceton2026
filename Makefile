PYTHON ?= python3.13

.PHONY: setup install install-stt install-phonetics dev dev-tunnel dev-all photon test test-client test-all

# System tools (python 3.13, ffmpeg, node, cloudflared). Installs only what is
# missing and never upgrades, so it is safe to re-run.
setup:
	./scripts/setup.sh

# Rebuilds .venv when its Python is gone (e.g. Homebrew upgraded python@3.13
# and cleaned up the old build), otherwise reuses it.
install:
	@if [ -d .venv ] && ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then \
		echo "[install] .venv's Python no longer exists; rebuilding .venv (re-run make install-stt if you use it)"; rm -rf .venv; fi
	@[ -d .venv ] || $(PYTHON) -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

# Optional: local speech-to-text (faster-whisper), the fallback when no saved
# phrase matches. Then pre-fetch the model: .venv/bin/python scripts/download_models.py --stt
install-stt:
	.venv/bin/pip install -r requirements-stt.txt

# Optional: the phonetic engine ("Learn from my sounds"): torch, transformers,
# CMUdict, wordfreq; then the word list and the ~1.2 GB phoneme model.
install-phonetics:
	.venv/bin/pip install -r requirements-phonetics.txt
	.venv/bin/python scripts/build_lexicon.py
	.venv/bin/python scripts/download_models.py --phonemes

dev:
	./run.sh

# Needed to test on an iPhone: Safari only grants the microphone over HTTPS.
dev-tunnel:
	./run.sh --tunnel

dev-all:
	./run.sh --all

photon:
	node photon-service/src/index.ts

test:
	.venv/bin/pytest -q

# jsdom-based suites (talk, recorder) need `npm install`; the rest do not.
test-client:
	node --test tests/*.test.mjs

test-all: test test-client
