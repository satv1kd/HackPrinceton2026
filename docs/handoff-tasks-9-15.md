# Tasks 9–15 — handoff

Branch: `feat/tasks-9-15`, based on `work/tasks-5-8` (which already contains
`main`), with the earlier `feat/tasks-13-14-15` branch merged in.

Task numbers in this file refer to the **15-task list in
`.taskmaster/tasks/tasks.json` on `main`**. An earlier branch in this repo
carried a different, 25-task parse of the same PRD; where the two collided,
the 15-task list won, and this file replaces `docs/tasks-13-14-15.md`, whose
numbers came from the superseded list.

Status: 9–15 are marked `done`. **5–8 are implemented in this tree but still
marked `pending`** — that is rnv-b's work and theirs to close out.

## What landed

| Task | What it is | Files |
|---|---|---|
| 9 | Setup Mode: phrase list, add-phrase form, 3-sample recorder | `static/js/setup.js` |
| 10 | ElevenLabs TTS with disk cache, and the client player | `app/services/tts.py`, `app/routers/tts.py`, `static/js/tts.js` |
| 11 | Correction flow on top of rnv-b's result card | `static/js/correction.js`, `static/js/talk.js` |
| 12 | Photon iMessage relay (separate Node service) | `photon-service/` |
| 13 | Contacts, send proxy, send buttons wired into the card | `app/services/contacts.py`, `app/routers/send.py`, `static/js/send-buttons.js` |
| 14 | HTTPS tunnel for iPhone Safari | `docs/https-tunnel.md`, `run.sh`, `Makefile` |
| 15 | `GET /api/health` with per-dependency checks | `app/services/health.py`, `app/routers/health.py` |

Task 11 was partly built already: rnv-b's `talk.js` had the result card,
Yes/No and the top-3 picker. What was missing — and is what landed — is the
correction flow behind **No** / **None of these**, the "Suggested" marker on
generated candidates, and speaking the confirmed result.

## The decisions worth knowing about

**The PRD is wrong about Photon.** It assumes a local macOS SDK that drives
Messages.app. Photon 2.0 is a hosted platform: you get an API key, a project,
and an assigned shared iMessage line. The consequence that bites is that **a
shared line cannot message someone first** — the recipient has to text your
assigned number once before you can send to them. `photon-service/README.md`
has the full correction and a setup checklist. The damage is contained to
`photon-service/src/transport.ts`; the `POST /send {to, text} -> {ok, error?}`
seam the rest of the app talks to is unchanged.

**Photon runs in stub mode by default.** With no credentials,
`photon-service` logs the send and returns ok, so the whole
confirm → send → sent path demos with no Photon account at all. `PHOTON_MODE=live`
forces the real transport and throws at startup if the key and project are
missing, so you cannot demo in stub mode by accident when you meant to send.

**Speech never blocks the confirm path.** `TTSPlayer.speak()` returns
`'elevenlabs' | 'browser' | 'silent'` and never rejects. `GET /api/tts`
returns **503** when ElevenLabs is unconfigured or failing, which is the
documented signal for the client to fall back to browser `speechSynthesis`.
The user has already been understood by that point; losing the voice must not
lose the sentence.

**Generated text is never spoken or sent unconfirmed.** `autoSpeak()` fires
only for a `confident` match whose candidate `type === 'phrase'`. Phonetic-
decoder output gets a "Suggested" badge and must be tapped first.

**Send buttons are tied to the text on screen.** They render only with
`confirmed === true` (and fail closed on truthy-but-not-`true`), and they are
cleared on a new utterance, on any re-render, and on undo. There is no path
where the buttons outlive the phrase they belong to.

**Failure policy on `/api/send`.** A *caller* mistake is an HTTP error (404
unknown contact, 422 empty text). A *downstream* failure — Photon down, timed
out, no conversation yet — returns `200 {"ok": false, "error": "..."}` so the
button shows a failed state with a reason instead of throwing. A broken
`contacts.json` is the exception and returns 500: rendering zero send buttons
would hide a setup error as "no contacts".

**`GET /api/contacts` withholds raw handles** and returns a `handle_hint`
(`…4567`) instead. The client sends by `contact_id`, so it never needs the
number, and a result card may be held up to a stranger at a counter.

**The health check never imports torch.** `check_phoneme_model()` uses
`importlib.util.find_spec`. `/api/health` is polled, and a check that loads a
1 GB model would be worse than no check. It always returns 200, even degraded;
`CRITICAL = ("ffmpeg", "disk_space")` decides the overall status, because
those two are what actually break recognition.

**`allow_origins=["*"]`** in `app/main.py`, credentials off. The tunnel
hostname is random and rotates per restart, so an allowlist is not
maintainable in development, and the app is single-user with no cookies.
This is a development posture — see the note at the end of
`docs/https-tunnel.md`.

## Setup you have to do by hand

```bash
cp .env.example .env            # fill in the keys you have
cp data/contacts.example.json data/contacts.json
brew install ffmpeg             # not optional; see below
```

- **ffmpeg is required.** Without it every recording fails with "Didn't catch
  that", which looks exactly like a microphone problem. `/api/health` reports
  it, which is why it is a critical check.
- **ElevenLabs** (`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`) is optional —
  without it the browser voice is used.
- **Photon** needs a beta account; `photon-service/README.md` has the
  checklist. Without it, stub mode.

```bash
./run.sh                 # server only
./run.sh --tunnel        # + cloudflared (TUNNEL=ngrok for ngrok)
./run.sh --all           # + photon-service
make test-all
```

## Known gaps

- **The new UI has not been opened in a browser.** Tasks 9, 11 and 13 are
  covered by `tests/client-ui.test.mjs` and `tests/send.test.mjs` against a
  DOM stub, not by a real render. The preview tooling in the environment this
  was built in resolved its launch config from the wrong directory. **Open
  Setup Mode and run one recording end to end before demoing.**
- **Photon's request shapes are unverified.** They come from the Photon docs,
  not from a live call — no account was available. The `listMessages` →
  `sendMessage` sequence in `transport.ts` is the thing most likely to need a
  small correction once a key exists.
- **17 Python tests fail on a machine without ffmpeg** (`test_audio.py`,
  `test_talk_integration.py`). That is the whole of the failure set: 200 pass.
  Install ffmpeg and they go green.
- **`tests/talk.test.mjs` needs `npm install`** — it imports jsdom, which is
  declared in `package.json` but not installed. Every other JS suite is
  dependency-free on purpose and runs on a clean checkout.
- **Phase 4 phonetics is out of scope of the 15-task list** but is in the
  tree (`app/phonetics/`), with `app/settings_bridge.py` as its config seam.
  Real-model latency is still unmeasured (the weights are ~1 GB):
  `python scripts/measure_phoneme_latency.py --budget 1.5 data/audio/`.

## Tests

```bash
python -m pytest tests/ -q      # 200 pass, 17 fail without ffmpeg
node --test tests/client-ui.test.mjs       # 31
node --test tests/photon-service.test.mjs  # 24
node --test tests/send.test.mjs            # 20
node --test tests/recorder.test.mjs        # 9
```
