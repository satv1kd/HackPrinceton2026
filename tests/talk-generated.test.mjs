// Task 20: generated results are confirmed before they are spoken, and
// confirmed results can be replayed.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

let dom, app, spoken, posts, count = 0;
const originalFetch = globalThis.fetch;
const ok = data => ({ ok: true, json: async () => data });
const feedbackResponse = { event_id: 'e1', undo_until: new Date(Date.now() + 10000).toISOString() };

const stt = text => ({ type: 'generated', source: 'stt', phrase_id: null, text, score: 0.2 });
const saved = (id, text, extra = {}) => ({ type: 'phrase', source: 'phrase', phrase_id: id, text, emoji: '💧', score: 0.1, ...extra });

beforeEach(async () => {
  dom = new JSDOM(readFileSync(new URL('../static/app.html', import.meta.url)), { url: 'http://localhost/' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.CustomEvent = dom.window.CustomEvent;
  globalThis.fetch = async () => ok([]);
  await import(`../static/js/app.js?generated=${count++}`);
  app = window.app;
  spoken = [];
  app.talk.tts.speak = async text => { spoken.push(text); return 'browser'; };
  app.talk.showSendButtons = async () => {};
  app.talk.recorder = { start: async cb => { cb(); return { blob: new Blob(['a']), mimeType: 'audio/webm' }; }, cancel() {}, stop() {} };
});

afterEach(() => {
  app.talk.cancel();
  clearTimeout(app.toastTimer);
  dom.window.close();
  globalThis.fetch = originalFetch;
});

function serve(result) {
  posts = [];
  globalThis.fetch = async (url, options) => {
    if (url === '/api/recognize') return ok(result);
    if (url === '/api/feedback') { posts.push(JSON.parse(options.body)); return ok(feedbackResponse); }
    return ok([]);
  };
}

const $ = id => document.getElementById(id);

test('a single speech-to-text guess shows as the result with Yes/No and is not spoken', async () => {
  serve({ attempt_id: 'a1', confident: false, stage: 'stt', candidates: [stt('Can I get a coffee')] });
  await app.talk.startRecording();
  assert.equal($('result-card').hidden, false);
  assert.equal($('feedback-buttons').hidden, false);
  assert.match($('result-text').textContent, /💬 Can I get a coffee/);
  assert.equal($('result-text').querySelector('.generated-badge').textContent, 'Best guess');
  assert.equal($('candidate-list').childElementCount, 0);
  assert.deepEqual(spoken, []);
  assert.equal($('replay-btn').hidden, true);
  assert.match($('talk-status').textContent, /Tap Yes to say it out loud/);
});

test('Yes on a generated guess speaks it once, labels it as text, and offers replay', async () => {
  serve({ attempt_id: 'a2', confident: false, stage: 'stt', candidates: [stt('See you tomorrow')] });
  await app.talk.startRecording();
  $('yes-btn').click();
  await new Promise(r => setImmediate(r));
  assert.deepEqual(posts, [{ attempt_id: 'a2', correct: true, label: { type: 'text', text: 'See you tomorrow' } }]);
  assert.deepEqual(spoken, ['See you tomorrow']);
  assert.equal($('replay-btn').hidden, false);
  $('replay-btn').click();
  assert.deepEqual(spoken, ['See you tomorrow', 'See you tomorrow']);
});

test('a confident saved phrase still auto-speaks exactly once, and Yes does not repeat it', async () => {
  serve({ attempt_id: 'a3', confident: true, stage: 'phrase', candidates: [saved('w', 'Water please')] });
  await app.talk.startRecording();
  assert.deepEqual(spoken, ['Water please']);
  assert.equal($('replay-btn').hidden, false);
  $('yes-btn').click();
  await new Promise(r => setImmediate(r));
  assert.deepEqual(spoken, ['Water please']);
  assert.equal($('replay-btn').hidden, false);
});

test('a phrase found by speech-to-text keeps its emoji and needs a Yes', async () => {
  serve({ attempt_id: 'a4', confident: false, stage: 'stt', candidates: [saved('w', 'I want water', { source: 'stt', score: 0 })] });
  await app.talk.startRecording();
  assert.match($('result-text').textContent, /💧 I want water/);
  assert.equal($('result-text').querySelector('.generated-badge'), null);
  assert.deepEqual(spoken, []);
  $('yes-btn').click();
  await new Promise(r => setImmediate(r));
  assert.deepEqual(posts[0].label, { type: 'phrase', phrase_id: 'w' });
  assert.deepEqual(spoken, ['I want water']);
});

test('several guesses become the picker with source badges', async () => {
  serve({ attempt_id: 'a5', confident: false, stage: 'stt', candidates: [
    stt('Can I get a coffee'), { type: 'generated', source: 'phonetic', text: 'red', score: 1 }, saved('w', 'Water')] });
  await app.talk.startRecording();
  assert.equal($('feedback-buttons').hidden, true);
  const buttons = [...$('candidate-list').querySelectorAll('button')];
  assert.deepEqual(buttons.map(b => b.querySelector('.generated-badge')?.textContent ?? null),
    ['Best guess', 'From your sounds', null, null]);
  assert.equal(buttons.at(-1).textContent, 'None of these');
  buttons[1].click();
  await new Promise(r => setImmediate(r));
  assert.deepEqual(spoken, ['red']);
});

test('a new recording hides replay so old words cannot be repeated by mistake', async () => {
  serve({ attempt_id: 'a6', confident: true, stage: 'phrase', candidates: [saved('w', 'Water please')] });
  await app.talk.startRecording();
  assert.equal($('replay-btn').hidden, false);
  serve({ attempt_id: 'a7', confident: false, stage: 'stt', candidates: [stt('Hello')] });
  await app.talk.startRecording();
  assert.equal($('replay-btn').hidden, true);
  $('replay-btn').click();
  assert.deepEqual(spoken, ['Water please']);
});

test('nothing recognized shows a helpful status and no card', async () => {
  serve({ attempt_id: 'a8', confident: false, stage: 'phrase', candidates: [] });
  await app.talk.startRecording();
  assert.equal($('result-card').hidden, true);
  assert.match($('talk-status').textContent, /didn’t catch that/);
});
