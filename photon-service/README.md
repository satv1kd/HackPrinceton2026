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
npm install                                  # pulls @photon-ai/api
# in ../.env:
#   PHOTON_API_KEY="..."                     # project API key
#   PHOTON_PROJECT_ID="..."
node src/index.ts                            # or: make photon
```

Anything already in the environment wins over the file, so
`PHOTON_MODE=stub make photon` still forces stub mode. `/health` reports which
mode it came up in.

| Variable | Meaning |
|---|---|
| `PHOTON_API_KEY` | Project API key. Needs `project:read` and `project:write`; assigning a line also needs `platforms:write`. |
| `PHOTON_PROJECT_ID` | The project the line belongs to. |
| `PHOTON_SENDER_ID` | Optional. The line to send from, needed only if the project has more than one; otherwise discovered. |
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
2. **Sending needs a resolved conversation, and your own line as sender.**
   `sendMessage` takes a `spaceId` and a `senderId`, and `senderId` must be
   *our line*, not the recipient. So a send is three calls:
   `createOrGetUser` (handle -> user), `createOrGetSpace` (our line + them ->
   conversation), then `sendMessage`. Both of the first two are idempotent.
   The line itself is found once via `listUsers`, picking the user that
   carries `details.type` (`shared` or `dedicated`); set `PHOTON_SENDER_ID`
   if a project has several. Handles go out as E.164, so `4155550123` in
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

**Verified against the real SDK.** `src/transport.ts` talks to Photon through
a hand-written `PhotonLikeClient` interface so the relay can run and be tested
with nothing installed. `src/photon-contract.ts` stops that interface drifting
from reality: it imports the type declarations published in
`@photon-ai/api@0.6.0` and asserts, at compile time, both that a real client
satisfies our interface and that the exact argument objects this service
builds are assignable to the SDK's own `$strict` input schemas. So the
namespace (`projects`), the `{ path, body, headers }` envelopes, the required
`idempotency-key` headers, the `{ items }` list envelope and every field name
are the SDK's, not a guess.

```bash
npm install && npm run typecheck   # fails if the shapes drift
```

That check earns its keep: it caught `getProject` wanting `path.projectId`
while the message, user and space operations want `path.id`.

The `SHARED_LINE_INBOUND_REQUIRED` and `SHARED_LINE_NOT_ASSIGNED` problem
codes are also real — they are in the SDK's generated error types, which is
independent confirmation of the "they must text you first" constraint above.

**Not verified.** No call has been made against the live API, because no
account was available. Two things only a real send can settle:

- `content.format: "plain"`. The SDK types it as a free `string`, so nothing
  checks the value. If Photon rejects it, that one literal in
  `src/transport.ts` is what changes.
- Whether the live service behaves as its own types claim, and whether
  `createOrGetSpace` or `sendMessage` is the call that refuses a contact who
  has not texted in. Both paths map to the same explanation, so either way the
  user sees the right message.

## Tests

```bash
node --test ../tests/photon-service.test.mjs
```

29 tests, no npm install and no Photon account: the SDK is injected as a fake
through `clientFactory`. `npm run typecheck` is the separate, stronger check
described above and does need `npm install`.
