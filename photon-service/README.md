# Photon relay

Relays a confirmed phrase from Heard to iMessage. FastAPI forwards to it; it
talks to Photon. Nothing else in the system knows Photon exists.

```
POST /send {to, text}  ->  {ok, error?, messageId?}
GET  /health           ->  {status, mode, imessage_connected, detail}
```

## Run it

No `npm install` needed to start — the relay has zero runtime dependencies
and Node runs the TypeScript directly (Node 22.6+).

```bash
node src/index.ts          # or: npm start
```

Without credentials it starts in **stub mode**: sends are logged, not
delivered, and `/send` returns `{ok: true}`. That is deliberate — the whole
confirm → send → sent path in the UI can be demoed with no Photon account.

To send for real:

```bash
npm install                                  # pulls @photon-ai/api
export PHOTON_API_KEY=...                    # project API key
export PHOTON_PROJECT_ID=...
node src/index.ts
```

| Variable | Meaning |
|---|---|
| `PHOTON_API_KEY` | Project API key. Needs `project:read` and `project:write`; assigning a line also needs `platforms:write`. |
| `PHOTON_PROJECT_ID` | The project the line belongs to. |
| `PHOTON_MODE` | `stub` or `live`. Defaults to `live` when both of the above are set, else `stub`. |
| `PORT` | Defaults to `3001`. Must match `PHOTON_SERVICE_URL` in the app's `.env`. |

## The PRD is wrong about Photon — read this

The PRD describes "a small TypeScript service [that] runs on a Mac signed
into Messages and uses Photon's local iMessage SDK". **That is not what
Photon 2.0 is.** Per photon.codes/docs/beta it is a *hosted* platform at
`https://api.photon.codes/v1` that owns the phone lines. There is no local
SDK, the Messages app is not involved, and **no Mac is required** — which
removes the PRD's "a Mac is required" infrastructure constraint.

Two consequences change the product, not just this service:

1. **A shared line cannot send an unsolicited first message.** The recipient
   has to message your assigned Photon number first — the docs say to send
   exactly `ping`, as an iMessage, not SMS. Until they do there is no
   conversation to reply into, so "Send to Mom" fails with a message saying
   precisely that.
2. **Sending is reply-shaped.** It needs `spaceId`, `senderId` and
   `parentMessageId` from an incoming message, so the relay looks up the
   conversation for a handle (`listMessages`) before it can send
   (`sendMessage`). A handle matches on its last ten digits, so
   `4155550123` in `contacts.json` matches `+1 (415) 555-0123` from Photon.

Also: Photon beta iMessage carries **text and images only** — no tapbacks,
effects or voice notes. Delivery is accepted asynchronously and *not
confirmed*, so `{ok: true}` means "accepted for dispatch", not "delivered".

## Setup checklist

1. Apply for the 2.0 beta at [photon.codes/beta](https://photon.codes/beta)
   and open the dashboard at `app.beta.photon.codes`.
2. Create a project; copy its id and a server-side API key.
3. Assign a shared iMessage line (needs a plan with shared iMessage
   capacity).
4. From the phone you will demo with, send `ping` as an **iMessage** to the
   assigned number. Repeat for every contact you want to send to.
5. Put the handle of each such contact in `data/contacts.json` (copy
   `data/contacts.example.json`).

## Unverified

The SDK call shapes in `src/transport.ts` come from the beta iMessage guide,
not from a successful live call — nobody has run this against a real Photon
project yet. The guide does not state `sendMessage`'s return shape, so the
`messageId` is read defensively. If the SDK differs, `src/transport.ts` is
the only file that changes.

## Tests

```bash
node --test ../tests/photon-service.test.mjs
```

24 tests, no npm install and no Photon account: the SDK is injected as a fake
through `clientFactory`.
