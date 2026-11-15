# Heard

**Siri and Google don't understand everyone. Heard learns your way of speaking in about 5 minutes, then speaks your words clearly and texts them for you.**

Heard is a mobile web app for adults with moderate to severe speech differences (Down syndrome, cerebral palsy, stroke, ALS, and others), including people who use personal words like "wawa" for water. You record a few phrases in your own voice. After that, you hold a button and speak, and Heard shows the phrase on a large card, says it aloud in a clear voice, and can send it as an iMessage.

> If Siri already understands you, you don't need this.

Built for HackPrinceton 2026. The full product spec is in [`.taskmaster/docs/prd.txt`](.taskmaster/docs/prd.txt).

## How it works

1. **Setup:** add a phrase (text plus an emoji or photo) and record it 3 times.
2. **Talk:** hold the big button and speak. The server compares your recording against every saved recording and picks the closest phrase.
3. **Unsure?** If no phrase is a clear winner, Heard shows the top 3 as big picture buttons to tap instead of guessing.
4. **Feedback:** large **✓ Yes** / **✕ No** buttons after every result. Each confirmed recording is saved as a new example, so recognition improves the more you use it.
5. **Speak and send:** the confirmed phrase is spoken with ElevenLabs (or the browser's built-in voice as a fallback) and can be sent to a contact over iMessage through [Photon](https://photon.codes).

Under the hood, the server converts every upload to 16 kHz mono WAV with ffmpeg, trims silence, normalizes volume and extracts MFCC features. It then uses dynamic time warping (DTW), which lines up recordings spoken at different speeds, to score each saved phrase. A phrase is accepted only if it beats the runner-up by a tunable margin.

## Repo layout

```
app/                    Python FastAPI server
  main.py               App entry point (routes, static files, CORS)
  config.py             Settings, loaded from .env
  routers/              HTTP endpoints: phrases, recognize, feedback, tts, send, health
  services/             Audio cleanup, MFCC features, DTW matcher, storage, TTS, contacts, health
  phonetics/            Phoneme recognizer + symbol inventory (in progress, not yet used by recognition)
  models/               Pydantic data models
static/                 The mobile web client (vanilla HTML/CSS/JS, no build step)
photon-service/         Small TypeScript relay that sends iMessages via Photon
scripts/                tune_thresholds.py, measure_phoneme_latency.py
tests/                  pytest suites (test_*.py) and Node client tests (*.test.mjs)
docs/                   https-tunnel.md (phone testing), handoff notes
data/                   Runtime data: phrases, recordings, caches (gitignored, created on first run)
.taskmaster/            Taskmaster tasks and the PRD
```

## Running it

### Quick start (copy and paste)

```bash
# From the repo root, on macOS with Homebrew
brew install python@3.13 ffmpeg node cloudflared   # skips anything already installed
make install                                       # Python environment in .venv
[ -f .env ] || cp .env.example .env                # settings file (keeps an existing one)
make dev-all                                       # server + HTTPS tunnel + iMessage relay
```

Open the `https://…trycloudflare.com` URL it prints on your phone, or http://localhost:8000 on your laptop. Stop with `Ctrl+C`. The sections below explain each step.

### Prerequisites

- **Python 3.13 or older.** librosa and numba don't support 3.14+ yet. `make install` uses `python3.13`; override with `make install PYTHON=python3.12`.
- **ffmpeg:** `brew install ffmpeg`. Without it, every recording fails with "Didn't catch that".
- **Node.js 22.6+:** only for the Photon iMessage relay and the client tests.
- **cloudflared:** only for testing on a phone: `brew install cloudflared`.

### 1. Install

```bash
make install
```

This creates `.venv/` and installs the Python dependencies. Run it again after pulling changes to `requirements*.txt`.

### 2. Configure (optional)

```bash
cp .env.example .env
```

Everything has a working default. The settings you're most likely to change:

| Variable | What it does |
|---|---|
| `ELEVENLABS_API_KEY` | Clear spoken output. If empty, the browser's built-in voice is used. |
| `ELEVENLABS_VOICE_ID` | Which ElevenLabs voice to use. |
| `MARGIN`, `ABSOLUTE_MAX` | How sure the matcher must be before it auto-accepts a phrase. Tune with `scripts/tune_thresholds.py`. |
| `PHOTON_API_KEY`, `PHOTON_PROJECT_ID` | Real iMessage sending. If empty, the relay runs in stub mode. |

### 3. Start

| Goal | Command | Then open |
|---|---|---|
| Use it on your laptop | `make dev` | http://localhost:8000 |
| Use it on your phone | `make dev-tunnel` | The `https://…trycloudflare.com` URL it prints |
| Phone + iMessage sending | `make dev-all` | Same tunnel URL |
| Just the iMessage relay | `make photon` | Listens on port 3001 |

**Why the phone needs a tunnel:** iPhone Safari only grants microphone access on HTTPS pages (`localhost` is the only exception). `make dev-tunnel` starts the server and a cloudflared tunnel together. Use `TUNNEL=ngrok make dev-tunnel` for ngrok instead. See [docs/https-tunnel.md](docs/https-tunnel.md) for troubleshooting.

Interactive API docs are at http://localhost:8000/docs, and http://localhost:8000/api/health reports whether ffmpeg, ElevenLabs and the Photon relay are available.

### 4. Use it

1. Open the app and go to **Setup**. Add a few phrases and record each one 3 times.
2. Switch to **Talk**, hold the button, say one of your phrases and release.
3. Tap **✓ Yes** or **✕ No**. Confirmed recordings become new training examples.

## iMessage sending (Photon)

The relay in `photon-service/` is the only part of the system that talks to Photon. FastAPI forwards confirmed phrases to it over `localhost`.

- **Stub mode (default):** with no Photon credentials, sends are logged, not delivered, so the whole flow can be demoed without an account.
- **Live mode:**
  ```bash
  cd photon-service && npm install
  export PHOTON_API_KEY=...  PHOTON_PROJECT_ID=...
  ```
  Then start it with `make photon` or `make dev-all`.
- **Recipients must message first.** Each recipient has to send `ping` (as an iMessage) to your Photon number once before Heard can text them.
- **Contacts:** contacts live in `data/contacts.json`, which you edit by hand:
  ```json
  [{ "id": "mom", "name": "Mom", "handle": "+15551234567", "emoji": "👩" }]
  ```

Details are in [photon-service/README.md](photon-service/README.md).

## Tests

```bash
make test         # Python tests (pytest)
npm install       # once, for the browser-side tests (jsdom)
make test-all     # Python + Node client tests
```

## Scripts

```bash
# Suggest MARGIN / ABSOLUTE_MAX from your own enrolled recordings
.venv/bin/python scripts/tune_thresholds.py

# Measure phoneme-model latency on WAV files (needs torch + transformers, not in requirements.txt)
.venv/bin/python scripts/measure_phoneme_latency.py data/audio/
```

## Status and roadmap

**Working:** setup, talk mode, top-3 picker, Yes/No feedback with undo, learning from confirmations, ElevenLabs TTS with browser fallback, Photon relay with contacts, and the HTTPS tunnel.

**In progress:** the phonetic generalization engine. Setup phrases will be chosen to cover every English sound, so Heard can learn how a person produces each sound and recognize common words they never recorded. The phoneme recognizer exists in `app/phonetics/` but isn't used by recognition yet.

Task-level status is tracked with Taskmaster: run `task-master list`.
