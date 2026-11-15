/**
 * Tests for the Photon relay (Task 12).
 *
 * Run with:  node --test tests/photon-service.test.mjs
 *
 * No npm install and no Photon account needed: the SDK is injected as a fake
 * through `clientFactory`, and the relay itself has no dependencies.
 *
 * The fake mirrors the Spectrum SDK's shape - `space.create(handle)` then
 * `space.send(text)`, with SpectrumCloudError-shaped failures carrying
 * `status` and `code`. A fake cannot prove those shapes are real;
 * `photon-service/src/spectrum-contract.ts` does that at compile time
 * against the SDK's own declarations.
 */

import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { chooseTransport, createHandler, readCredentials } from "../photon-service/src/index.ts";
import {
  createSpectrumTransport,
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

/* ----------------------------------------------------------- fake spectrum */

/**
 * A stand-in for the Spectrum app.
 *
 * `space.create(handle)` then `space.send(text)` is the whole surface this
 * service uses; `src/spectrum-contract.ts` is what proves those shapes are
 * the SDK's own, since no fake can.
 */
function fakeSpectrum({ failCreate = null, failSend = null, failStart = null, messageId = "msg-1" } = {}) {
  const created = [];
  const sent = [];
  let starts = 0;
  let stopped = 0;
  const appFactory = async () => {
    starts += 1;
    if (failStart) throw failStart;
    return {
      space: {
        async create(users) {
          created.push(users);
          if (failCreate) throw failCreate;
          return {
            id: `space-${handleKey(String(users))}`,
            async send(text) {
              if (failSend) throw failSend;
              sent.push({ users, text });
              return { id: messageId };
            },
          };
        },
      },
      async stop() {
        stopped += 1;
      },
    };
  };
  return { appFactory, created, sent, get starts() { return starts; }, get stopped() { return stopped; } };
}

/** A SpectrumCloudError-shaped rejection: status + code, as the SDK throws. */
function cloudError(status, code, message = code) {
  return Object.assign(new Error(message), { status, code });
}

function fakeCloud({
  project = { name: "stutter", profile: { imessageSynced: true } },
  tokens = { type: "shared" },
  fail = null,
} = {}) {
  return async () => ({
    async getProject() {
      if (fail) throw fail;
      return project;
    },
    async issueImessageTokens() {
      return tokens;
    },
  });
}

function liveTransport(fake, overrides = {}) {
  return createSpectrumTransport({
    projectId: "proj-1",
    projectSecret: "secret-1",
    appFactory: fake.appFactory,
    cloudFactory: fakeCloud(),
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
  const env = { PHOTON_PROJECT_SECRET: "s", PHOTON_PROJECT_ID: "p" };
  assert.equal(chooseTransport(env).mode, "live");
  assert.equal(chooseTransport({ ...env, PHOTON_MODE: "stub" }).mode, "stub");
});

test("the credentials are taken under any of the names they arrive with", () => {
  // PROJECT_ID / PROJECT_SECRET is what Photon's own scaffold writes, and
  // PHOTON_API_KEY is what this service asked for in its first version.
  assert.equal(chooseTransport({ PROJECT_ID: "p", PROJECT_SECRET: "s" }).mode, "live");
  assert.equal(chooseTransport({ PHOTON_PROJECT_ID: "p", PHOTON_API_KEY: "s" }).mode, "live");
  assert.deepEqual(
    readCredentials({ PROJECT_ID: "a", PHOTON_PROJECT_ID: "b", PROJECT_SECRET: "c" }),
    { projectId: "b", projectSecret: "c" },   // the documented name wins
  );
  assert.deepEqual(readCredentials({ PHOTON_PROJECT_ID: "  " }), { projectId: "", projectSecret: "" });
});

test("PHOTON_MODE=live without both credentials fails loudly", () => {
  assert.throws(
    () => chooseTransport({ PHOTON_MODE: "live", PHOTON_PROJECT_SECRET: "s" }),
    /PHOTON_PROJECT_ID/,
  );
});

/* ------------------------------------------------------------------ live */

test("a send opens the conversation with the contact and sends into it", async () => {
  const fake = fakeSpectrum();
  const result = await liveTransport(fake).send("+14155550123", "I want water");

  assert.equal(result.ok, true);
  assert.equal(result.messageId, "msg-1");
  assert.deepEqual(fake.created, ["+14155550123"]);
  assert.deepEqual(fake.sent, [{ users: "+14155550123", text: "I want water" }]);
});

test("the recipient handle reaches the SDK in E.164", async () => {
  for (const handle of ["+14155550123", "4155550123", "(415) 555-0123", "415-555-0123"]) {
    const fake = fakeSpectrum();
    const result = await liveTransport(fake).send(handle, "hi");
    assert.equal(result.ok, true, `handle ${handle} should send`);
    assert.deepEqual(fake.created, ["+14155550123"], `handle ${handle}`);
  }
});

test("an Apple ID email handle is passed through lowercased", async () => {
  const fake = fakeSpectrum();
  assert.equal((await liveTransport(fake).send("Mom@Example.com", "hi")).ok, true);
  assert.deepEqual(fake.created, ["mom@example.com"]);
});

test("a contact who never texted the line gets an explainable failure", async () => {
  // A shared Photon line cannot send first. This is the most likely live
  // failure, so the message has to tell the user what to do about it.
  const fake = fakeSpectrum({ failCreate: cloudError(403, "SHARED_LINE_INBOUND_REQUIRED") });
  const result = await liveTransport(fake).send("+19998887777", "hello");

  assert.equal(result.ok, false);
  assert.match(result.error, /cannot send first/);
  assert.match(result.error, /ping/);
  assert.match(result.error, /\+19998887777/, "name who has to text in");
  assert.equal(fake.sent.length, 0, "must not send with no conversation");
});

test("the same refusal on the send itself is explained the same way", async () => {
  const fake = fakeSpectrum({ failSend: cloudError(403, "SHARED_LINE_INBOUND_REQUIRED") });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /cannot send first/);
});

test("the inbound rule is recognised from prose as well as from a code", async () => {
  // The code strings are not published anywhere this service can check, so
  // the wording is a second way in to the one explanation that matters.
  const fake = fakeSpectrum({ failSend: new Error("no conversation with this user yet") });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.match(result.error, /cannot send first/);
});

test("an unassigned line is named as a setup mistake", async () => {
  const fake = fakeSpectrum({ failCreate: cloudError(403, "SHARED_LINE_NOT_ASSIGNED") });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /no iMessage line is assigned/);
});

test("the credential failures are distinguished by status", async () => {
  const cases = [
    [cloudError(401, "UNAUTHORIZED"), /PHOTON_PROJECT_SECRET/],
    [cloudError(403, "FORBIDDEN"), /different project|no phone connected/],
    [cloudError(404, "NOT_FOUND"), /PHOTON_PROJECT_ID/],
    [cloudError(402, "BILLING_SUSPENDED"), /credits/],
  ];
  for (const [error, pattern] of cases) {
    const fake = fakeSpectrum({ failStart: error });
    const result = await liveTransport(fake).send("+14155550123", "hi");
    assert.equal(result.ok, false, String(error.code));
    assert.match(result.error, pattern, String(error.code));
  }
});

test("the live refusal names both things that have to be true", async () => {
  // Seen against a real project: `space.send` answers "[spectrum-imessage]
  // Target not allowed for this project", with no code and no hint which of
  // the two setup steps is missing.
  const fake = fakeSpectrum({
    failSend: new Error("[spectrum-imessage] Target not allowed for this project"),
  });
  const result = await liveTransport(fake).send("+15551234567", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /phone connected/);
  assert.match(result.error, /ping/);
  assert.match(result.error, /\+15551234567/);
});

test("an unsynced profile is reported, never treated as a failure", async () => {
  // A live send succeeded with `imessageSynced: false`, so health must not
  // refuse a setup that works - it says so and stays green.
  const fake = fakeSpectrum();
  const health = await liveTransport(fake, {
    cloudFactory: fakeCloud({ project: { name: "stutter", profile: { imessageSynced: false } } }),
  }).health();
  assert.equal(health.ok, true);
  assert.match(health.detail, /profile not synced/);
});

test("an unrecognised failure keeps its raw message", async () => {
  const fake = fakeSpectrum({ failSend: new Error("429 rate limited") });
  const result = await liveTransport(fake).send("+14155550123", "hi");
  assert.equal(result.ok, false);
  assert.match(result.error, /429/);
});

test("one Spectrum app serves every send", async () => {
  const fake = fakeSpectrum();
  const transport = liveTransport(fake);
  await Promise.all([
    transport.send("+14155550123", "one"),
    transport.send("+14155550123", "two"),
  ]);
  await transport.send("+14155550123", "three");
  assert.equal(fake.starts, 1, "even two sends arriving together start one app");
  assert.equal(fake.sent.length, 3);
});

test("a failed start is not cached, so a fix in the dashboard takes effect", async () => {
  let fail = cloudError(403, "FORBIDDEN");
  const fake = fakeSpectrum();
  const transport = createSpectrumTransport({
    projectId: "proj-1",
    projectSecret: "secret-1",
    cloudFactory: fakeCloud(),
    appFactory: async () => {
      if (fail) throw fail;
      return (await fake.appFactory());
    },
  });
  assert.equal((await transport.send("+14155550123", "hi")).ok, false);
  fail = null;
  assert.equal((await transport.send("+14155550123", "hi")).ok, true);
});

test("stopping shuts the app down, and is safe with nothing started", async () => {
  const fake = fakeSpectrum();
  const transport = liveTransport(fake);
  await transport.stop();                  // nothing started yet
  await transport.send("+14155550123", "hi");
  await transport.stop();
  assert.equal(fake.stopped, 1);
});

test("live health fails without credentials, and probes the project and the line", async () => {
  const fake = fakeSpectrum();
  const bare = createSpectrumTransport({
    projectId: "", projectSecret: "", appFactory: fake.appFactory, cloudFactory: fakeCloud(),
  });
  const health = await bare.health();
  assert.equal(health.ok, false);
  assert.match(health.detail, /PHOTON_PROJECT_SECRET/);

  const good = await liveTransport(fake).health();
  assert.equal(good.ok, true);
  assert.match(good.detail, /shared iMessage line on stutter/);

  const unreachable = await liveTransport(fake, {
    cloudFactory: fakeCloud({ fail: new Error("ETIMEDOUT") }),
  }).health();
  assert.equal(unreachable.ok, false);
  assert.match(unreachable.detail, /unreachable/);

  // Reachable but nothing can be sent is not healthy, and is worth catching
  // before a demo rather than on the first send.
  const lineless = await liveTransport(fake, {
    cloudFactory: fakeCloud({ tokens: {} }),
  }).health();
  assert.equal(lineless.ok, false);
  assert.match(lineless.detail, /no iMessage line/);

  // A bad secret says so, rather than "unreachable" and nothing else.
  const refused = await liveTransport(fake, {
    cloudFactory: fakeCloud({ fail: cloudError(401, "UNAUTHORIZED") }),
  }).health();
  assert.match(refused.detail, /PHOTON_PROJECT_SECRET/);

  // Health must not start a Spectrum app: it is called on every page load.
  assert.equal(fake.starts, 0);
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
  const fake = fakeSpectrum();
  const { status, json } = await call(createHandler(liveTransport(fake)), "POST", "/send", {
    to: "+14155550123",
    text: "I want water",
  });

  assert.equal(status, 200);
  assert.equal(json.ok, true);
  assert.equal(fake.sent[0].text, "I want water");
});

test("a failed send is still HTTP 200 with the reason", async () => {
  // FastAPI and the UI read {ok, error}; an HTTP error would lose the reason.
  const fake = fakeSpectrum({ failCreate: cloudError(403, "SHARED_LINE_INBOUND_REQUIRED") });
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
  const fake = fakeSpectrum();
  await call(createHandler(liveTransport(fake)), "POST", "/send", {
    to: "+14155550123",
    text: "  hello  ",
  });
  assert.equal(fake.sent[0].text, "hello");
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
    'PHOTON_PROJECT_SECRET="pk_live_example"',
    "PHOTON_PROJECT_ID=proj_123   # the project the line belongs to",
    'PHOTON_SENDER_ID=',   // an unknown key is still parsed
    'export PHOTON_MODE=live',
    'NOT A LINE',
  ].join('\n'));

  const env = { PHOTON_MODE: 'stub' };   // already set: must not be overwritten
  loadEnvFile(file, env);
  assert.equal(env.PHOTON_PROJECT_SECRET, 'pk_live_example');
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
