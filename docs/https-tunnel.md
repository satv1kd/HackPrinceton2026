# HTTPS tunnel, for testing on a phone

**Why this is mandatory, not optional.** iPhone Safari only grants
`getUserMedia` on a secure origin. Over plain `http://192.168.x.x:8000` the
microphone prompt never appears and hold-to-talk silently does nothing — so
the whole app is untestable on the device it is designed for until a tunnel
is running. `localhost` is exempt, which is why it works on the laptop and
then appears broken on the phone.

## Cloudflared (recommended)

No account, no token, no bandwidth cap.

```bash
brew install cloudflared
```

```bash
cloudflared tunnel --url http://localhost:8000
```

It prints a URL like `https://random-three-words.trycloudflare.com`. Open
that on the phone. The URL changes every restart, which matters because
Safari ties microphone permission to the origin: a new URL means a fresh
permission prompt.

## ngrok (alternative)

```bash
brew install ngrok
ngrok config add-authtoken YOUR_TOKEN   # free account required
ngrok http 8000
```

A free ngrok URL also rotates per restart. Paid plans give a stable
subdomain, which is worth it if you are re-testing permissions often.

## Both at once

`./run.sh --tunnel` starts the server and a tunnel together and cleans both
up on exit:

```bash
./run.sh --tunnel              # cloudflared
TUNNEL=ngrok ./run.sh --tunnel # ngrok
make dev-tunnel                # same as ./run.sh --tunnel
```

## Verifying on the phone

1. `./run.sh --tunnel`
2. Open the printed HTTPS URL in Safari.
3. Press and hold **Hold to talk**. Safari asks for the microphone.
4. Allow, then say a phrase you have enrolled and release.
5. The result card should appear and speak.

Check `GET /api/health` in the phone's browser too — it will tell you if
ffmpeg is missing on the server, which breaks every recording with a
"Didn't catch that" error that looks like a microphone problem.

## Troubleshooting

| Symptom | Cause |
|---|---|
| No microphone prompt | The URL is `http://`, not `https://`. Only `localhost` is exempt. |
| Prompt denied and never reappears | Safari remembers the refusal per origin. Settings → Safari → Clear History and Website Data, or restart the tunnel for a fresh URL. |
| Recording uploads but always "Didn't catch that" | ffmpeg is not installed on the machine running the server. `brew install ffmpeg`. |
| Works in Chrome, silent in Safari | Safari will not play audio outside a user gesture. Speech is triggered from the release of hold-to-talk and from Yes/picker taps, all gestures — if you added a new code path that speaks, it needs one too. |
| Page loads, buttons do nothing | Check the console: ES modules fail hard on a syntax error. |
| CORS errors in the console | The server allows all origins (`app/main.py`), since the tunnel hostname is unpredictable. If you see CORS errors anyway, you are hitting a different server than you think. |

## Note on `allow_origins=["*"]`

`app/main.py` allows every origin, with credentials off. The tunnel hostname
is random and changes per restart, so an allowlist is not maintainable during
development, and the app is single-user with no auth and no cookies. **This
is a development posture.** Anything deployed for real needs a fixed origin
list, and that is the point at which the "no auth" decision in the PRD needs
revisiting too.
