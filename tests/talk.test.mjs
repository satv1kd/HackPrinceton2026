import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

let dom, app, count = 0;
const originalFetch = globalThis.fetch;
const tick = () => new Promise(resolve => setImmediate(resolve));
const result = () => ({ attempt_id: 'one', confident: true, candidates: [
  { type: 'phrase', phrase_id: 'water', text: 'Water please', score: 0.1, emoji: '💧' },
  { type: 'phrase', phrase_id: 'help', text: 'Help please', score: 0.8 }
] });
const ok = data => ({ ok: true, json: async () => data });

beforeEach(async () => {
  dom = new JSDOM(readFileSync(new URL('../static/index.html', import.meta.url)), { url: 'http://localhost/' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.CustomEvent = dom.window.CustomEvent;
  globalThis.fetch = async () => ok([]);
  await import(`../static/js/app.js?case=${count++}`);
  app = window.app;
});

afterEach(() => {
  app.talk.cancel();
  clearTimeout(app.toastTimer);
  dom.window.close();
  globalThis.fetch = originalFetch;
});

test('mode switching updates tab semantics and uses real phrase response', async () => {
  globalThis.fetch = async () => ok([{ text: 'Water', samples: [{ id: 's1' }], emoji: '💧' }]);
  app.switchMode('setup');
  await tick();
  assert.equal(document.getElementById('setup-tab').getAttribute('aria-selected'), 'true');
  assert.equal(document.getElementById('talk-mode').hidden, true);
  assert.match(document.getElementById('phrase-list').textContent, /Water.*1 recordings/);
  app.switchMode('talk');
  assert.equal(document.getElementById('talk-mode').hidden, false);
});

test('recognition upload shows a result and explicit feedback choices', async () => {
  let request;
  app.talk.recorder = { start: async callback => { callback(); return { blob: new Blob(['audio']), mimeType: 'audio/mp4' }; }, cancel() {} };
  globalThis.fetch = async (url, options) => { request = { url, options }; return ok(result()); };
  await app.talk.startRecording();
  assert.equal(request.url, '/api/recognize');
  assert.equal(request.options.body.get('audio').name, 'recording.m4a');
  assert.equal(document.getElementById('result-card').hidden, false);
  assert.equal(document.getElementById('feedback-buttons').hidden, false);
  assert.match(document.getElementById('result-text').textContent, /Water please/);
  assert.equal(app.talk.state, 'idle');
});

test('cancelling prevents a late recognition response from replacing the screen', async () => {
  let resolve;
  app.talk.recorder = { start: async callback => { callback(); return { blob: new Blob(['audio']), mimeType: 'audio/webm' }; }, cancel() {} };
  globalThis.fetch = () => new Promise(done => { resolve = done; });
  const pending = app.talk.startRecording();
  await tick();
  assert.equal(app.talk.state, 'uploading');
  app.talk.cancel();
  resolve(ok(result()));
  await pending;
  assert.equal(app.talk.result, null);
  assert.equal(document.getElementById('result-card').hidden, true);
});

test('duplicate feedback clicks send once; undo targets the original attempt', async () => {
  const sent = [];
  let resolve;
  globalThis.fetch = async (url, options) => {
    sent.push({ url, body: JSON.parse(options.body) });
    if (url === '/api/feedback') return new Promise(done => { resolve = done; });
    return ok({ ok: true });
  };
  app.talk.result = result();
  app.talk.showResult();
  const saving = app.talk.sendFeedback(true, app.talk.result.candidates[0]);
  await app.talk.sendFeedback(true, app.talk.result.candidates[0]);
  assert.equal(sent.length, 1);
  resolve(ok({ event_id: 'event-one', undo_until: new Date(Date.now() + 10000).toISOString() }));
  await saving;
  app.talk.result = { ...result(), attempt_id: 'two' };
  await document.getElementById('toast-action').onclick();
  assert.deepEqual(sent[1], { url: '/api/feedback/undo', body: { attempt_id: 'one', event_id: 'event-one' } });
  assert.equal(app.talk.result.attempt_id, 'two');
});

test('unsure selection submits the chosen phrase and renders text safely', async () => {
  const responses = [];
  app.talk.result = { ...result(), confident: false };
  app.talk.result.candidates[1].text = '<img src=x onerror=alert(1)>';
  app.talk.showResult();
  assert.equal(document.querySelector('#candidate-list img'), null);
  globalThis.fetch = async (_url, options) => {
    responses.push(JSON.parse(options.body));
    return ok({ event_id: 'e', undo_until: new Date(Date.now() + 10000).toISOString() });
  };
  await document.querySelectorAll('#candidate-list button')[1].onclick();
  assert.deepEqual(responses[0].label, { type: 'phrase', phrase_id: 'help' });
  assert.match(document.getElementById('result-text').textContent, /<img/);
  assert.equal(document.querySelector('#result-text img'), null);
});

test('No records negative-only feedback; failure keeps controls retryable', async () => {
  let sent;
  app.talk.result = result();
  app.talk.showResult();
  globalThis.fetch = async (_url, options) => {
    sent = JSON.parse(options.body);
    return { ok: false, json: async () => ({ detail: 'Please retry shortly' }) };
  };
  await app.talk.sendFeedback(false, null);
  assert.equal(sent.correct, false);
  assert.equal(sent.label, null);
  assert.equal(document.getElementById('yes-btn').disabled, false);
  assert.equal(document.getElementById('toast').hidden, true);
  assert.match(app.talk.status.textContent, /retry/);
});
