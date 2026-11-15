/**
 * Tests for the Photon relay (Task 12).
 *
 * Run with:  node --test tests/photon-service.test.mjs
 *
 * No npm install and no Photon account needed: the SDK is injected as a fake
 * through `clientFactory`, and the relay itself has no dependencies.
 *
 * The fake's call shapes mirror `@photon-ai/api@0.6.0`'s published type
 * declarations — one `{ path, body?, query?, headers? }` argument per
 * operation, `{ items }` for lists, and ApiError-shaped failures carrying a
 * problem `code` on `.body`. That is as close as this can get without an
 * account; nothing here proves the live API agrees.
 */

import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { chooseTransport, createHandler } from "../photon-service/src/index.ts";
import {
  createPhotonTransport,
  createStubTransport,
  handleKey,
  normalizeHandle,
  toPhotonHandle,
} from "../photon-service/src/transport.ts";

/* ----------------------------------------------------------- http helpers */

/** Drive the handler without binding a socket. */
async function call(handler, method, path, body) {
  const request = {
    method,
    url: path,
    listeners: {},
    on(event, fn) {
      (this.listeners[event] ||= []).push(fn);
      return this;
    },
    destroy() {},
  };

  const response = {
    status: null,
    body: "",
    writeHead(status) {
      this.status = status;
    },
    end(payload) {
      this.body = payload ?? "";
    },
  };

  const pending = handler(request, response);

  if (method === "POST") {
    const text = typeof body === "string" ? body : JSON.stringify(body ?? {});
    for (const fn of request.listeners.data ?? []) fn(Buffer.from(text, "utf8"));
    for (const fn of request.listeners.end ?? []) fn();
  }

  await pending;
  return { status: response.status, json: response.body ? JSON.parse(response.body) : null };
}

/* ------------------------------------------------------------- fake photon */

/** Our own shared line, as Photon lists it: a user carrying `details.type`. */
const LINE = {
  id: "line-1",
  handle: "+14155559999",
  platform: "imessage",
  details: { type: "shared", resourceId: "res-1" },
};

/** An ApiError-shaped rejection, as @photon-ai/api throws it. */
function apiError(status, code, message = code) {
  return Object.assign(new Error(message), { status, body: { code, status } });
}

function fakePhoton({
  lines = [LINE],
  spaceId = "space-1",
  onSend = null,
  failGet = null,
  failListUsers = null,
  failUser = null,
  failSpace = null,
} = {}) {
  const sent = [];
  const users = [];
  const spaces = [];
  const client = {
    projects: {
      async get(input) {
        if (failGet) throw failGet;
        return { id: input.path.id };
      },
      async listUsers(input) {
        if (failListUsers) throw failListUsers;
        return { items: lines, nextPageToken: undefined };
      },
      async createOrGetUser(input) {
        if (failUser) throw failUser;
        users.push(input);
        return { id: `user-${handleKey(input.body.handle)}`, handle: input.body.handle };
      },
      async createOrGetSpace(input) {
        if (failSpace) throw failSpace;
        spaces.push(input);
        return { id: spaceId };
      },
      async sendMessage(args) {
        sent.push(args);
        if (onSend) return onSend(args);
        return { id: "msg-1", status: "accepted" };
      },
    },
  };
  return { client, sent, users, spaces };
}

function liveTransport(fake, overrides = {}) {
  return createPhotonTransport({
    apiKey: "key",
    projectId: "proj-1",
    clientFactory: async () => fake.client,
    ...overrides,
  });
}

/* ------------------------------------------------------------ stub / mode */

test("stub transport logs instead of sending", async () => {
  const logged = [];
  const transport = createStubTransport((message) => logged.push(message));
  const result = await transport.send("+14155550123", "I want water");

  assert.equal(result.ok, true);
  assert.equal(transport.mode, "stub");
  assert.match(logged[0], /would send to \+14155550123: I want water/);
  assert.equal((await transport.health()).ok, true);
});

test("no credentials selects stub mode", () => {
  assert.equal(chooseTransport({}).mode, "stub");
});

test("credentials select live mode, and PHOTON_MODE can force either", () => {
  const env = { PHOTON_API_KEY: "k", PHOTON_PROJECT_ID: "p" };
  assert.equal(chooseTransport(env).mode, "live");
  assert.equal(chooseTransport({ ...env, PHOTON_MODE: "stub" }).mode, "stub");
});

test("PHOTON_MODE=live without both credentials fails loudly", () => {
  assert.throws(
    () => chooseTransport({ PHOTON_MODE: "live", PHOTON_API_KEY: "k" }),
    /PHOTON_PROJECT_ID/,
  );
});

/* ------------------------------------------------------------------ live */

test("sends from our line into the contact's space", async () => {
  const fake = fakePhoton();
  const result = await liveTransport(fake).send("+14155550123", "I want water");

  assert.equal(result.ok, true);
  assert.equal(result.messageId, "msg-1");
  assert.equal(fake.sent.length, 1);

  const args = fake.sent[0];
  assert.deepEqual(args.path, { id: "proj-1" });
  assert.equal(args.body.spaceId, "space-1");
  // The sender is OUR line, never the recipient: Photon rejects anything else.
  assert.equal(args.body.senderId, "line-1");
  assert.deepEqual(args.body.content, { type: "text", format: "plain", text: "I want water" });
  assert.ok(args.headers["idempotency-key"], "retries must not duplicate a send");

  // The space is created between our line and the resolved recipient.
  assert.deepEqual(fake.spaces[0].body.participantIds, ["line-1", "user-4155550123"]);
  assert.equal(fake.spaces[0].body.platform, "imessage");
  assert.ok(fake.spaces[0].headers["idempotency-key"]);
});

test("the recipient handle is sent to Photon in E.164", async () => {
  for (const handle of ["+14155550123", "4155550123", "(415) 555-0123", "415-555-0123"]) {
    const fake = fakePhoton();
    const result = await liveTransport(fake).send(handle, "hi");
    assert.equal(result.ok, true, `handle ${handle} should send`);
    assert.equal(fake.users[0].body.handle, "+14155550123", `handle ${handle}`);
    assert.equal(fake.users[0].body.platform, "imessage");
  }
});

test("an Apple ID email handle is passed through lowercased", async () => {
  const fake = fakePhoton();
  assert.equal((await liveTransport(fake).send("Mom@Example.com", "hi")).ok, true);
  assert.equal(fake.users[0].body.handle, "mom@example.com");
});

test("a contact who never texted the line gets an explainable failure", async () => {
  // A shared Photon line cannot send first. This is the most likely live
  // failure, so the message has to tell the user what to do about it.
  const fake = fakePhoton({
    failSpace: apiError(403, "SHARED_LINE_INBOUND_REQUIRED"),
  });
  const result = await liveTransport(fake).send("+19998887777", "hello");

  assert.equal(result.ok, false);
  assert.match(result.error, /cannot send first/);
  assert.match(result.error, /ping/);
  assert.match(result.error, /\+19998887777/, "name who has to text in");
  assert.equal(fake.sent.length, 0, "must not attempt a send with no space");
});

test("the same refusal on the send itself is explained the same way", async () => {
  const fake = fakePhoton({
    onSend: () => {
      throw apiError(403, "SHARED_LINE_INBOUND_REQUIRED");
    },
  });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /cannot send first/);
});

test("an unassigned shared line is named as a setup mistake", async () => {
  const fake = fakePhoton({ failSpace: apiError(403, "SHARED_LINE_NOT_ASSIGNED") });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /no shared iMessage line is assigned/);
});

test("a bad API key and a missing scope are distinguished", async () => {
  const cases = [
    ["NOT_AUTHENTICATED", /PHOTON_API_KEY/],
    ["INSUFFICIENT_SCOPE", /project:write/],
    ["BILLING_SUSPENDED", /credits/],
  ];
  for (const [code, pattern] of cases) {
    const fake = fakePhoton({ failUser: apiError(401, code) });
    const result = await liveTransport(fake).send("+14155550123", "hi");
    assert.equal(result.ok, false, code);
    assert.match(result.error, pattern, code);
  }
});

test("a project with no line explains itself instead of sending", async () => {
  const fake = fakePhoton({ lines: [] });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /no iMessage line found/);
  assert.equal(fake.sent.length, 0);
});

test("contacts are not mistaken for our line", async () => {
  // A contact has no details.type; only a line does. Picking a contact as
  // the sender would fail at Photon with a confusing error.
  const contact = { id: "user-9", handle: "+14155550123", platform: "imessage", details: {} };
  const fake = fakePhoton({ lines: [contact] });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /no iMessage line found/);
});

test("two lines are ambiguous unless PHOTON_SENDER_ID picks one", async () => {
  const second = { ...LINE, id: "line-2", handle: "+14155558888" };
  const fake = fakePhoton({ lines: [LINE, second] });

  const ambiguous = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(ambiguous.ok, false);
  assert.match(ambiguous.error, /PHOTON_SENDER_ID/);
  assert.match(ambiguous.error, /\+14155559999/, "list the candidates");
  assert.equal(fake.sent.length, 0);

  const pinned = await liveTransport(fake, { senderId: "line-2" }).send("+14155550123", "hi");
  assert.equal(pinned.ok, true);
  assert.equal(fake.sent[0].body.senderId, "line-2");
});

test("PHOTON_SENDER_ID is read from the environment", () => {
  const transport = chooseTransport({
    PHOTON_API_KEY: "k",
    PHOTON_PROJECT_ID: "p",
    PHOTON_SENDER_ID: "line-7",
  });
  assert.equal(transport.mode, "live");
});

test("an unrecognised failure keeps its raw message", async () => {
  const fake = fakePhoton({
    onSend: () => {
      throw new Error("429 rate limited");
    },
  });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /429/);
});

test("the sending line is looked up once and reused", async () => {
  let listed = 0;
  const fake = fakePhoton();
  const original = fake.client.projects.listUsers;
  fake.client.projects.listUsers = async (input) => {
    listed += 1;
    return original(input);
  };
  const transport = liveTransport(fake);
  await transport.send("+14155550123", "one");
  await transport.send("+14155550123", "two");
  assert.equal(listed, 1, "the line does not change while the service runs");
});

test("live health fails without credentials, and probes project and line", async () => {
  const bare = createPhotonTransport({
    apiKey: "",
    projectId: "",
    clientFactory: async () => fakePhoton().client,
  });
  const health = await bare.health();
  assert.equal(health.ok, false);
  assert.match(health.detail, /PHOTON_API_KEY/);

  const good = await liveTransport(fakePhoton()).health();
  assert.equal(good.ok, true);
  assert.match(good.detail, /line-1/);

  const unreachable = await liveTransport(fakePhoton({ failGet: new Error("ETIMEDOUT") })).health();
  assert.equal(unreachable.ok, false);
  assert.match(unreachable.detail, /unreachable/);

  // Reachable but nothing can be sent is not healthy, and is worth catching
  // before a demo rather than on the first send.
  const lineless = await liveTransport(fakePhoton({ lines: [] })).health();
  assert.equal(lineless.ok, false);
  assert.match(lineless.detail, /no iMessage line found/);
});

/* ----------------------------------------------------------------- handles */

test("normalizeHandle strips phone punctuation and lowercases email", () => {
  assert.equal(normalizeHandle("+1 (415) 555-0123"), "14155550123");
  assert.equal(normalizeHandle("  Mom@Example.COM "), "mom@example.com");
});

test("handleKey matches a number with and without its country code", () => {
  // contacts.json may hold a bare number while Photon reports E.164.
  assert.equal(handleKey("+14155550123"), handleKey("4155550123"));
  assert.equal(handleKey("(415) 555-0123"), handleKey("+1 415 555 0123"));
  // Different numbers must still differ.
  assert.notEqual(handleKey("4155550123"), handleKey("4155550124"));
  // Short local numbers are not truncated into false matches.
  assert.equal(handleKey("5550123"), "5550123");
});

test("toPhotonHandle produces E.164, and keeps emails intact", () => {
  assert.equal(toPhotonHandle("4155550123"), "+14155550123");
  assert.equal(toPhotonHandle("(415) 555-0123"), "+14155550123");
  assert.equal(toPhotonHandle("+14155550123"), "+14155550123");
  assert.equal(toPhotonHandle("+44 20 7946 0958"), "+442079460958");
  assert.equal(toPhotonHandle("Mom@Example.com"), "mom@example.com");
});

/* ------------------------------------------------------------- http layer */

test("GET /health reports mode and project", async () => {
  const handler = createHandler(createStubTransport(() => {}), "proj-1");
  const { status, json } = await call(handler, "GET", "/health");

  assert.equal(status, 200);
  assert.equal(json.status, "ok");
  assert.equal(json.mode, "stub");
  assert.equal(json.imessage_connected, true);
  assert.equal(json.project_id, "proj-1");
});

test("GET /health is 503 when the transport is unhealthy", async () => {
  const unhealthy = {
    mode: "live",
    async send() {
      return { ok: false };
    },
    async health() {
      return { ok: false, detail: "no credentials" };
    },
  };
  const { status, json } = await call(createHandler(unhealthy), "GET", "/health");
  assert.equal(status, 503);
  assert.equal(json.status, "unavailable");
});

test("POST /send forwards to the transport", async () => {
  const fake = fakePhoton();
  const { status, json } = await call(createHandler(liveTransport(fake)), "POST", "/send", {
    to: "+14155550123",
    text: "I want water",
  });

  assert.equal(status, 200);
  assert.equal(json.ok, true);
  assert.equal(fake.sent[0].body.content.text, "I want water");
});

test("a failed send is still HTTP 200 with the reason", async () => {
  // FastAPI and the UI read {ok, error}; an HTTP error would lose the reason.
  const fake = fakePhoton({ failSpace: apiError(403, "SHARED_LINE_INBOUND_REQUIRED") });
  const handler = createHandler(liveTransport(fake));
  const { status, json } = await call(handler, "POST", "/send", { to: "+1999", text: "hi" });

  assert.equal(status, 200);
  assert.equal(json.ok, false);
  assert.match(json.error, /cannot send first/);
});

test("POST /send validates its body", async () => {
  const handler = createHandler(createStubTransport(() => {}));
  for (const body of [{}, { to: "+1415" }, { text: "hi" }, { to: "  ", text: "hi" }]) {
    const { status, json } = await call(handler, "POST", "/send", body);
    assert.equal(status, 400, JSON.stringify(body));
    assert.match(json.error, /required/);
  }
});

test("POST /send rejects non-JSON", async () => {
  const handler = createHandler(createStubTransport(() => {}));
  const { status, json } = await call(handler, "POST", "/send", "not json at all");
  assert.equal(status, 400);
  assert.match(json.error, /must be JSON/);
});

test("text is trimmed before sending", async () => {
  const fake = fakePhoton();
  await call(createHandler(liveTransport(fake)), "POST", "/send", {
    to: "+14155550123",
    text: "  hello  ",
  });
  assert.equal(fake.sent[0].body.content.text, "hello");
});

test("unknown routes are 404", async () => {
  const { status } = await call(createHandler(createStubTransport(() => {})), "GET", "/nope");
  assert.equal(status, 404);
});

/* ---- .env is the one place credentials go ------------------------------- */

test('the relay reads the project .env, and the environment still wins', async () => {
  const { loadEnvFile } = await import('../photon-service/src/index.ts');
  const file = join(await mkdtemp(join(tmpdir(), 'heard-env-')), '.env');
  await writeFile(file, [
    '# a comment',
    'PHOTON_API_KEY="pk_live_example"',
    "PHOTON_PROJECT_ID=proj_123   # the project the line belongs to",
    'PHOTON_SENDER_ID=',
    'export PHOTON_MODE=live',
    'NOT A LINE',
  ].join('\n'));

  const env = { PHOTON_MODE: 'stub' };   // already set: must not be overwritten
  loadEnvFile(file, env);
  assert.equal(env.PHOTON_API_KEY, 'pk_live_example');
  assert.equal(env.PHOTON_PROJECT_ID, 'proj_123');   // trailing comment stripped
  assert.equal(env.PHOTON_SENDER_ID, '');
  assert.equal(env.PHOTON_MODE, 'stub');
  assert.equal('NOT' in env, false);
});

test('a missing .env is not an error - it just means stub mode', async () => {
  const { loadEnvFile } = await import('../photon-service/src/index.ts');
  const env = {};
  loadEnvFile('/no/such/.env', env);
  assert.deepEqual(env, {});
});
