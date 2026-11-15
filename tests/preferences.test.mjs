// Task 21: the phonetic-mode switch and the Talk screen indicator.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

let dom, app, requests, count = 0;
const originalFetch = globalThis.fetch;
const tick = () => new Promise(resolve => setImmediate(resolve));
const json = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

const prefs = (over = {}) => ({ phonetic_mode: false, phonetic_available: false,
  phonetic_unavailable_reason: 'Record the sound set first', stt_available: true, ...over });

async function boot(server) {
  requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method || 'GET', body: options.body });
    return server(url, options);
  };
  dom = new JSDOM(readFileSync(new URL('../static/app.html', import.meta.url)), { url: 'http://localhost/' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.CustomEvent = dom.window.CustomEvent;
  await import(`../static/js/app.js?prefs=${count++}`);
  app = window.app;
  await tick();
}

afterEach(() => {
  app?.talk.cancel();
  clearTimeout(app?.toastTimer);
  dom?.window.close();
  globalThis.fetch = originalFetch;
});

const $ = id => document.getElementById(id);

test('the switch reflects the stored preference and the reason it is not ready', async () => {
  await boot(url => json(url === '/api/preferences' ? prefs({ phonetic_mode: true }) : []));
  const sw = $('phonetic-switch');
  assert.equal(sw.getAttribute('role'), 'switch');
  assert.equal(sw.getAttribute('aria-checked'), 'true');
  assert.match(sw.textContent, /Learn from my sounds/);
  assert.equal($('phonetic-switch-reason').hidden, false);
  assert.equal($('phonetic-switch-reason').textContent, 'Record the sound set first');
  assert.equal($('listening-mode').textContent,
    'Listening for: your phrases · best guess (sound learning isn’t ready yet)');
});

test('clicking sends PUT with the flipped value and updates both screens', async () => {
  await boot((url, options) => {
    if (url !== '/api/preferences') return json([]);
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      return json(prefs({ ...body, phonetic_available: true, phonetic_unavailable_reason: null }));
    }
    return json(prefs());
  });
  $('phonetic-switch').click();
  await tick();
  const put = requests.find(r => r.method === 'PUT');
  assert.deepEqual(JSON.parse(put.body), { phonetic_mode: true });
  assert.equal($('phonetic-switch').getAttribute('aria-checked'), 'true');
  assert.equal($('phonetic-switch-reason').hidden, true);
  assert.equal($('listening-mode').textContent, 'Listening for: your phrases · your sounds · best guess');
});

test('a failed PUT reverts the switch and shows a toast', async () => {
  await boot((url, options) => {
    if (url !== '/api/preferences') return json([]);
    if (options.method === 'PUT') return json({ detail: 'disk full' }, 500);
    return json(prefs());
  });
  $('phonetic-switch').click();
  assert.equal($('phonetic-switch').getAttribute('aria-checked'), 'true'); // optimistic
  await tick(); await tick();
  assert.equal($('phonetic-switch').getAttribute('aria-checked'), 'false');
  assert.equal($('toast').hidden, false);
  assert.match($('toast-message').textContent, /Couldn’t change that setting/);
});

test('without speech-to-text the indicator lists only what will run', async () => {
  await boot(url => json(url === '/api/preferences' ? prefs({ stt_available: false }) : []));
  assert.equal($('listening-mode').textContent, 'Listening for: your phrases');
});

test('a missing or malformed preferences response falls back to mode off', async () => {
  await boot(() => json([]));
  assert.equal($('phonetic-switch').getAttribute('aria-checked'), 'false');
  assert.equal($('listening-mode').textContent, 'Listening for: your phrases');
});

test('normalizePreferences ignores wrong types', async () => {
  await boot(() => json([]));
  const { normalizePreferences } = await import('../static/js/preferences.js');
  assert.deepEqual(normalizePreferences({ phonetic_mode: 'yes', stt_available: true, extra: 1 }),
    { phonetic_mode: false, phonetic_available: false, phonetic_unavailable_reason: null, stt_available: true });
});

test('the enroll button appears only when not ready', async () => {
  await boot(url => json(url === '/api/preferences' ? prefs() : []));
  const { PhoneticToggle } = await import('../static/js/preferences.js');
  let opened = 0;
  const box = document.createElement('div');
  document.body.append(box);
  const toggle = new PhoneticToggle(box, app.preferences, { onEnroll: () => opened++ });
  assert.equal(toggle.enrollButton.hidden, false);
  toggle.enrollButton.click();
  assert.equal(opened, 1);
  toggle.update(prefs({ phonetic_available: true }));
  assert.equal(toggle.enrollButton.hidden, true);
});
