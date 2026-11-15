PYTHON ?= python3.13

.PHONY: install install-stt dev dev-tunnel dev-all photon test test-client test-all

install:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

# Optional: local speech-to-text (faster-whisper), the fallback when no saved
# phrase matches. Then pre-fetch the model: .venv/bin/python scripts/download_models.py --stt
install-stt:
	.venv/bin/pip install -r requirements-stt.txt

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
