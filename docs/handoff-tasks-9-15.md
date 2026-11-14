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

**Sending needs our own line as the sender.** `sendMessage` wants a
`spaceId` and a `senderId`, and `senderId` is *our* Photon line, not the
recipient — so a send resolves the contact (`createOrGetUser`), then the
conversation (`createOrGetSpace`), then sends. The line is discovered once
from `listUsers` by its `details.type`; `PHOTON_SENDER_ID` pins it when a
project has several, which is refused rather than guessed, because a shared
line only accepts replies on the number the contact actually texted.

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

- **Nothing has been through a real microphone.** The UI has been driven in
  a browser, and the audio path has been exercised end to end with
  synthesised speech (see "What the audio path actually does" below), but no
  human has spoken into it. A person with a speech difference is the case the
  whole app exists for and the one thing none of this covers.
- **`/api/send` has never reached Photon**, since there is no account. The
  relay was exercised end to end in stub mode.
- **Photon has never been called live.** The request shapes are now checked
  at compile time against `@photon-ai/api@0.6.0`'s published declarations
  (`photon-service/src/photon-contract.ts`, run by `npm run typecheck`), so
  the field names are the SDK's rather than a guess. What is still open is
  behaviour: whether `content.format: "plain"` is accepted (the SDK types it
  as a free string), and whether the live service matches its own types.
- **ffmpeg is required**, and 17 Python tests fail without it. With it
  installed the suite is 217/217.
- **`tests/talk.test.mjs` needs `npm install`** — it imports jsdom. Every
  other JS suite is dependency-free on purpose and runs on a clean checkout.
- **Phase 4 phonetics is out of scope of the 15-task list** but is in the
  tree (`app/phonetics/`), with `app/settings_bridge.py` as its config seam.
  Real-model latency is still unmeasured (the weights are ~1 GB):
  `python scripts/measure_phoneme_latency.py --budget 1.5 data/audio/`.

## What the audio path actually does

Enrolled two phrases with three synthesised samples each (macOS `say`), then
recognised fresh utterances. Scores are path-normalised cosine distance, so
lower is better; `MARGIN` is 0.15 and `ABSOLUTE_MAX` is 0.75.

**Same voice, different speaking rate** — the easy case:

| Said | Top match | Score | Runner-up | Confident |
|---|---|---|---|---|
| I want water | I want water | ~0 | 0.69 | yes |
| I need the bathroom | I need the bathroom | ~0 | 0.67 | yes |

Near-zero because `say` is deterministic and DTW normalises time away, so
this proves the pipeline runs — ffmpeg decode, MFCC, DTW, ranking — and
nothing about how it behaves on a human.

**Enrolled with one voice, probed with another** (US female → UK male), which
is a harsher test than the app actually faces:

| Said | Top match | Score | Runner-up | Confident |
|---|---|---|---|---|
| I want water | I want water | 0.7519 | 0.8150 | no |
| I need the bathroom | I need the bathroom | 0.6677 | 0.7694 | no |

The ranking is still right, and `confident: false` is the correct answer — a
voice the app has not been enrolled on should go to the top-3 picker, not be
spoken aloud. This is the app being speaker-dependent on purpose.

One number to watch: 0.7519 is a hair over `ABSOLUTE_MAX` (0.75), so that
match was one rounding away from being dropped entirely and showing "no
match". `config.py` already says to tune that on real recordings. **Do it
with the actual speaker before the demo** — too low and their words are
rejected outright, which is the worst failure this app can have.

## Tests

```bash
python -m pytest tests/ -q      # 217 pass (needs ffmpeg)
node --test tests/client-ui.test.mjs       # 32
node --test tests/photon-service.test.mjs  # 29
node --test tests/send.test.mjs            # 20
node --test tests/recorder.test.mjs        # 9
node --test tests/talk.test.mjs            # 6  (needs npm install for jsdom)
cd photon-service && npm run typecheck     # Photon shapes vs the real SDK
```
