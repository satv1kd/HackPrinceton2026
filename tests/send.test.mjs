/**
 * Tests for static/js/send.js (Task 13).
 *
 * Run with:  node --test tests/js/
 *
 * Uses a minimal DOM stub rather than jsdom so the suite needs no npm
 * install. send.js touches a small, stable slice of the DOM — createElement,
 * appendChild, dataset, textContent, addEventListener — which is cheap to
 * fake and keeps the test honest about what the module actually does.
 */

import assert from "node:assert/strict";
import { test, beforeEach } from "node:test";

// ---------------------------------------------------------------------------
// DOM stub
// ---------------------------------------------------------------------------

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this.textContent = "";
    this.disabled = false;
    this.title = "";
  }

  set innerHTML(value) {
    if (value === "") this.children = [];
  }
  get innerHTML() {
    return this.children.map((child) => child.textContent).join("");
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }

  /** Fire a click, awaiting the async handlers send.js registers. */
  async click() {
    for (const handler of this.listeners.click || []) await handler();
  }

  /** Depth-first search for descendants of a given class. */
  findAll(className) {
    const found = [];
    for (const child of this.children) {
      if (child.className === className) found.push(child);
      found.push(...child.findAll(className));
    }
    return found;
  }
}

globalThis.document = {
  createElement: (tagName) => new FakeElement(tagName),
};

// ---------------------------------------------------------------------------
// fetch stub
// ---------------------------------------------------------------------------

const CONTACTS = [
  { id: "mom", name: "Mom", emoji: "👩", handle_hint: "…4567" },
  { id: "cafe", name: "Café", emoji: "☕", handle_hint: "c…@example.com" },
];

let calls;
let contactsReply;
let sendReply;

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

globalThis.fetch = async (url, options = {}) => {
  calls.push({ url: String(url), options });
  if (String(url).includes("/api/contacts")) return contactsReply();
  if (String(url).includes("/api/send")) return sendReply();
  throw new Error(`unstubbed ${url}`);
};

beforeEach(() => {
  calls = [];
  contactsReply = () => jsonResponse(CONTACTS);
  sendReply = () => jsonResponse({ ok: true });
});

/** Fresh module instance, so the module-level contacts cache never leaks. */
let moduleCounter = 0;
async function loadModule() {
  return import(`../static/js/send-buttons.js?v=${moduleCounter++}`);
}

const sendCalls = () => calls.filter((call) => call.url.includes("/api/send"));
const bodyOf = (call) => JSON.parse(call.options.body);

async function render(overrides = {}) {
  const { renderSendButtons } = await loadModule();
  const container = new FakeElement("div");
  const statuses = [];
  await renderSendButtons({
    container,
    text: "I want water",
    confirmed: true,
    onStatus: (state, detail) => statuses.push({ state, detail }),
    ...overrides,
  });
  return { container, statuses, buttons: container.findAll("send-button") };
}

// ---------------------------------------------------------------------------
// The confirmation gate
// ---------------------------------------------------------------------------

test("renders nothing when the result is not confirmed", async () => {
  const { container } = await render({ confirmed: false });
  assert.equal(container.children.length, 0);
  assert.equal(calls.length, 0, "must not even fetch contacts");
});

test("fails closed when confirmed is omitted or truthy-but-not-true", async () => {
  for (const confirmed of [undefined, null, 1, "yes", {}]) {
    const { container } = await render({ confirmed });
    assert.equal(container.children.length, 0, `confirmed=${String(confirmed)}`);
  }
});

test("renders nothing for blank text even when confirmed", async () => {
  for (const text of ["", "   ", null, undefined]) {
    const { container } = await render({ text });
    assert.equal(container.children.length, 0);
  }
});

test("clears the container before re-rendering", async () => {
  const { renderSendButtons } = await loadModule();
  const container = new FakeElement("div");
  container.appendChild(new FakeElement("p"));
  await renderSendButtons({ container, text: "hi", confirmed: false });
  assert.equal(container.children.length, 0);
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

test("renders one large button per contact", async () => {
  const { buttons } = await render();
  assert.equal(buttons.length, 2);
  assert.equal(buttons[0].textContent, "👩 Send to Mom");
  assert.equal(buttons[1].textContent, "☕ Send to Café");
  assert.equal(buttons[0].dataset.contactId, "mom");
  assert.equal(buttons[0].dataset.state, "idle");
  assert.equal(buttons[0].attributes["aria-label"], "Send to Mom");
  assert.equal(buttons[0].type, "button");
});

test("handles a contact with no emoji", async () => {
  contactsReply = () => jsonResponse([{ id: "bus", name: "Bus driver" }]);
  const { buttons } = await render();
  assert.equal(buttons[0].textContent, "Send to Bus driver");
});

test("says so when no contacts are configured", async () => {
  contactsReply = () => jsonResponse([]);
  const { container } = await render();
  const notices = container.findAll("send-notice");
  assert.equal(notices.length, 1);
  assert.equal(notices[0].textContent, "No contacts set up yet.");
});

test("surfaces a broken contacts endpoint instead of rendering nothing", async () => {
  contactsReply = () => jsonResponse({ detail: "boom" }, 500);
  const { container } = await render();
  const notices = container.findAll("send-notice");
  assert.equal(notices.length, 1);
  assert.match(notices[0].textContent, /Can't load contacts/);
  assert.match(notices[0].textContent, /HTTP 500/);
});

test("contacts are fetched once and reused across renders", async () => {
  const { renderSendButtons } = await loadModule();
  for (let i = 0; i < 3; i++) {
    await renderSendButtons({
      container: new FakeElement("div"),
      text: "hi",
      confirmed: true,
    });
  }
  assert.equal(calls.filter((c) => c.url.includes("/api/contacts")).length, 1);
});

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

test("a tap posts the confirmed text and the contact id", async () => {
  const { buttons } = await render();
  await buttons[0].click();

  assert.equal(sendCalls().length, 1);
  const call = sendCalls()[0];
  assert.equal(call.options.method, "POST");
  assert.equal(call.options.headers["Content-Type"], "application/json");
  assert.deepEqual(bodyOf(call), { text: "I want water", contact_id: "mom" });
});

test("text is trimmed before it is sent", async () => {
  const { buttons } = await render({ text: "   I want water  " });
  await buttons[0].click();
  assert.equal(bodyOf(sendCalls()[0]).text, "I want water");
});

test("a successful send shows a sent state with an icon, not colour alone", async () => {
  const { buttons, statuses } = await render();
  await buttons[0].click();

  assert.equal(buttons[0].dataset.state, "sent");
  assert.equal(buttons[0].textContent, "✓ Sent to Mom");
  assert.equal(buttons[0].disabled, true);
  assert.deepEqual(statuses.map((s) => s.state), ["sending", "sent"]);
});

test("a failed send re-enables the button and keeps the error", async () => {
  sendReply = () => jsonResponse({ ok: false, error: "Messages not signed in" });
  const { buttons, statuses } = await render();
  await buttons[0].click();

  assert.equal(buttons[0].dataset.state, "failed");
  assert.match(buttons[0].textContent, /✗ Didn't send/);
  assert.equal(buttons[0].disabled, false, "the user must be able to retry");
  assert.equal(buttons[0].title, "Messages not signed in");
  assert.deepEqual(statuses.map((s) => s.state), ["sending", "failed"]);
  assert.equal(statuses[1].detail.error, "Messages not signed in");
});

test("a network error is a failed send, not an unhandled rejection", async () => {
  sendReply = () => {
    throw new TypeError("Failed to fetch");
  };
  const { buttons } = await render();
  await buttons[0].click();
  assert.equal(buttons[0].dataset.state, "failed");
  assert.equal(buttons[0].title, "Failed to fetch");
});

test("an HTTP error detail from the server reaches the button", async () => {
  sendReply = () => jsonResponse({ detail: "no contact with id 'nobody'" }, 404);
  const { buttons } = await render();
  await buttons[0].click();
  assert.equal(buttons[0].dataset.state, "failed");
  assert.equal(buttons[0].title, "no contact with id 'nobody'");
});

test("an HTTP error with no JSON body falls back to the status code", async () => {
  sendReply = () => ({
    ok: false,
    status: 502,
    json: async () => {
      throw new Error("not json");
    },
  });
  const { buttons } = await render();
  await buttons[0].click();
  assert.equal(buttons[0].title, "HTTP 502");
});

test("a response without ok:true is not treated as sent", async () => {
  sendReply = () => jsonResponse({ status: "maybe" });
  const { buttons } = await render();
  await buttons[0].click();
  assert.equal(buttons[0].dataset.state, "failed");
});

// ---------------------------------------------------------------------------
// Mis-tap protection (motor impairment is a stated constraint)
// ---------------------------------------------------------------------------

test("tapping an already-sent button does not send twice", async () => {
  const { buttons } = await render();
  await buttons[0].click();
  await buttons[0].click();
  await buttons[0].click();
  assert.equal(sendCalls().length, 1);
});

test("a failed send can be retried by tapping again", async () => {
  let attempt = 0;
  sendReply = () => (++attempt === 1 ? jsonResponse({ ok: false, error: "down" }) : jsonResponse({ ok: true }));
  const { buttons } = await render();

  await buttons[0].click();
  assert.equal(buttons[0].dataset.state, "failed");
  await buttons[0].click();
  assert.equal(buttons[0].dataset.state, "sent");
  assert.equal(sendCalls().length, 2);
});

test("each contact's button sends only to that contact", async () => {
  const { buttons } = await render();
  await buttons[1].click();
  assert.equal(sendCalls().length, 1);
  assert.equal(bodyOf(sendCalls()[0]).contact_id, "cafe");
});
