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

To send for real, put the two values in the project's `.env` — the relay
reads it on startup, so there is no second place to set them:

```bash
npm install                                  # pulls spectrum-ts
# in ../.env:
#   PHOTON_PROJECT_ID="..."                  # dashboard -> Project ID
#   PHOTON_PROJECT_SECRET="..."              # dashboard -> Project secret
node src/index.ts                            # or: make photon
```

Anything already in the environment wins over the file, so
`PHOTON_MODE=stub make photon` still forces stub mode. `/health` reports which
mode it came up in.

| Variable | Meaning |
|---|---|
| `PHOTON_PROJECT_SECRET` | The project secret from the dashboard. The SDK exchanges it for tokens; it is never sent as a bearer token. |
| `PHOTON_PROJECT_ID` | The project the line belongs to. |

`PROJECT_ID` / `PROJECT_SECRET` — the names Photon's own
`bun create spectrum-project` scaffold writes — are accepted too, as is
`PHOTON_API_KEY` for the secret, which is what this service asked for in its
first version.
| `PHOTON_MODE` | `stub` or `live`. Defaults to `live` when both of the above are set, else `stub`. |
| `PORT` | Defaults to `3001`. Must match `PHOTON_SERVICE_URL` in the app's `.env`. |

## Which Photon API this talks to

Photon has two, and they take different credentials.

| | `@photon-ai/api` | `spectrum-ts` (this service) |
|---|---|---|
| What it is | Account/organization REST API | The Spectrum SDK |
| Credential | `pho_sk_…` / `pho_ask_…`, as `Authorization: Bearer` | `projectId` + `projectSecret`, exchanged for tokens by the SDK |
| Where the credential comes from | An account that issues API keys | The two values on the dashboard's Get started page |

This service was written against the first one, and every call answered
`403 FORBIDDEN` with a Spectrum project secret — the credential is simply not
one that API accepts. If a future dashboard does hand out a `pho_sk_…` key,
that is a different door, not a better one: sending still goes through
Spectrum here.

Sending is two calls: `space.create(handle)` resolves or opens the 1:1
conversation, then `space.send(text)`. The iMessage provider's own types say a
user id is "the canonical address (E.164 phone or email)", which is the format
`toPhotonHandle` produces.

One shape worth knowing: Spectrum is built around a long-running agent loop
over `app.messages`. This relay never reads that stream — it holds one app
open for the process's life and sends into it per request. Nothing in the SDK
forbids it; it is not the shape the examples use.

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
2. **Sending needs a resolved conversation.** `space.create(handle)` is what
   resolves or opens it; the line to send from is the project's own, so there
   is nothing to configure. Handles go out as E.164, so `4155550123` in
   `contacts.json` is sent as `+14155550123`.

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
   assigned number. Repeat for every contact you want to send to. Nothing can
   be sent to a phone that has not done this - that is Photon's rule for
   shared lines, not ours.
5. Put the handle of each such contact in `data/contacts.json` (copy
   `data/contacts.example.json`). The file is gitignored: it holds real phone
   numbers.
6. Check it end to end: `curl localhost:3001/health` should say
   `"mode":"live"`, and `/api/contacts` on the app lists each contact with
   only the last four digits.

## What is verified, and what is not

**Verified against the real SDK.** `src/transport.ts` talks to Spectrum
through hand-written interfaces so the relay can run and be tested with
nothing installed. `src/spectrum-contract.ts` stops those drifting from
reality: it imports `spectrum-ts` and writes out the exact send path —
`Spectrum({projectId, projectSecret, providers:[imessage.config()]})`,
`imessage(app).space.create(handle)`, `space.send(text)` — plus the two health
probes, so `tsc` fails if any of it stops matching the SDK.

```bash
npm install && npm run typecheck   # fails if the shapes drift
```

That check earns its keep twice over: in the previous version it caught
`getProject` wanting `path.projectId` while the other operations wanted
`path.id`, and here it is what confirms `space.create` accepts a bare handle
string.

**Verified against the live API.** With a real project secret:

- `cloud.getProject` and `cloud.issueImessageTokens` both answer (the latter
  reporting a `shared` line), `Spectrum()` starts, and `space.create` returns
  the conversation `{id: "any;-;+1…", type: "dm", phone: "shared"}`.
- `cloud.getImessageInfo` and `cloud.getSubscription` take *no* secret and
  answer `401 Missing authorization header` — they are for a signed-in
  dashboard session. That is why health probes `issueImessageTokens`.

**Not delivered yet.** `space.send` answers
`[spectrum-imessage] Target not allowed for this project`, because the Photon
account has no phone connected (`project.profile.imessageSynced` is `false`),
so no target is enrolled. Health checks that flag now and says so rather than
letting a demo find out. What a successful send returns, and which of the two
setup steps Photon is objecting to, are still unknown.

Also unknown: whether holding one Spectrum app open and sending into it per
HTTP request behaves over hours rather than over one call.

## Tests

```bash
node --test ../tests/photon-service.test.mjs
```

31 tests, no npm install and no Photon account: the SDK is injected as a fake
through `clientFactory`. `npm run typecheck` is the separate, stronger check
described above and does need `npm install`.
