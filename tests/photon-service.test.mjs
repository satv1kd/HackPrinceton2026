/**
 * Tests for the Photon relay (Task 12).
 *
 * Run with:  node --test tests/photon-service.test.mjs
 *
 * No npm install and no Photon account needed: the SDK is injected as a fake
 * through `clientFactory`, and the relay itself has no dependencies.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { chooseTransport, createHandler } from "../photon-service/src/index.ts";
import {
  createPhotonTransport,
  createStubTransport,
  handleKey,
  normalizeHandle,
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

function fakePhoton({ messages = [], onSend = null, failList = null } = {}) {
  const sent = [];
  const client = {
    projects: {
      async listMessages() {
        if (failList) throw failList;
        return { data: messages };
      },
      async sendMessage(args) {
        sent.push(args);
        if (onSend) return onSend(args);
        return { id: "msg-1" };
      },
    },
  };
  return { client, sent };
}

const MOM = {
  id: "incoming-9",
  spaceId: "space-1",
  senderId: "agent-1",
  from: "+1 (415) 555-0123",
  createdAt: "2026-11-14T10:00:00Z",
};

function liveTransport(fake, projectId = "proj-1") {
  return createPhotonTransport({
    apiKey: "key",
    projectId,
    clientFactory: async () => fake.client,
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

test("sends into the conversation the contact opened", async () => {
  const fake = fakePhoton({ messages: [MOM] });
  const result = await liveTransport(fake).send("+14155550123", "I want water");

  assert.equal(result.ok, true);
  assert.equal(result.messageId, "msg-1");
  assert.equal(fake.sent.length, 1);

  const args = fake.sent[0];
  assert.deepEqual(args.path, { id: "proj-1" });
  assert.equal(args.body.spaceId, "space-1");
  assert.equal(args.body.senderId, "agent-1");
  assert.equal(args.body.parentMessageId, "incoming-9");
  assert.deepEqual(args.body.content, { type: "text", format: "plain", text: "I want water" });
  assert.ok(args.headers["idempotency-key"], "retries must not duplicate a send");
});

test("a contact who never messaged the line gets an explainable failure", async () => {
  // A shared Photon line cannot send first. This is the most likely live
  // failure, so the message has to tell the user what to do about it.
  const fake = fakePhoton({ messages: [MOM] });
  const result = await liveTransport(fake).send("+19998887777", "hello");

  assert.equal(result.ok, false);
  assert.match(result.error, /cannot send first/);
  assert.match(result.error, /ping/);
  assert.equal(fake.sent.length, 0, "must not attempt a send with no conversation");
});

test("handles match across formatting differences", async () => {
  for (const handle of ["+14155550123", "4155550123", "(415) 555-0123", "415-555-0123"]) {
    const fake = fakePhoton({ messages: [MOM] });
    const result = await liveTransport(fake).send(handle, "hi");
    assert.equal(result.ok, true, `handle ${handle} should match`);
  }
});

test("email handles match case-insensitively", async () => {
  const fake = fakePhoton({ messages: [{ ...MOM, from: "Mom@Example.com" }] });
  assert.equal((await liveTransport(fake).send("mom@example.com", "hi")).ok, true);
});

test("replies into the newest conversation when several match", async () => {
  const older = { ...MOM, id: "old", spaceId: "space-old", createdAt: "2026-11-01T00:00:00Z" };
  const newer = { ...MOM, id: "new", spaceId: "space-new", createdAt: "2026-11-14T12:00:00Z" };
  const fake = fakePhoton({ messages: [older, newer] });

  await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(fake.sent[0].body.spaceId, "space-new");
});

test("alternative handle fields are recognised", async () => {
  for (const message of [
    { ...MOM, from: undefined, handle: "+14155550123" },
    { ...MOM, from: undefined, author: { handle: "+14155550123" } },
  ]) {
    const fake = fakePhoton({ messages: [message] });
    assert.equal((await liveTransport(fake).send("+14155550123", "hi")).ok, true);
  }
});

test("a conversation missing spaceId is reported, not sent blindly", async () => {
  const fake = fakePhoton({ messages: [{ ...MOM, spaceId: undefined }] });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /spaceId/);
  assert.equal(fake.sent.length, 0);
});

test("a listMessages failure is a readable error", async () => {
  const fake = fakePhoton({ failList: new Error("401 unauthorized") });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /could not read Photon conversations/);
  assert.match(result.error, /401/);
});

test("a sendMessage failure is a readable error", async () => {
  const fake = fakePhoton({
    messages: [MOM],
    onSend: () => {
      throw new Error("429 rate limited");
    },
  });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /429/);
});

test("live health fails without credentials and probes the API with them", async () => {
  const bare = createPhotonTransport({
    apiKey: "",
    projectId: "",
    clientFactory: async () => fakePhoton().client,
  });
  const health = await bare.health();
  assert.equal(health.ok, false);
  assert.match(health.detail, /PHOTON_API_KEY/);

  assert.equal((await liveTransport(fakePhoton()).health()).ok, true);

  const broken = await liveTransport(fakePhoton({ failList: new Error("ETIMEDOUT") })).health();
  assert.equal(broken.ok, false);
  assert.match(broken.detail, /unreachable/);
});

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
  const fake = fakePhoton({ messages: [MOM] });
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
  const handler = createHandler(liveTransport(fakePhoton({ messages: [] })));
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
  const fake = fakePhoton({ messages: [MOM] });
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
