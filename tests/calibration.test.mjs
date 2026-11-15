/* The calibration card: record the whole sentence, then press "I've read it".

   The live word-by-word highlight is gone on purpose, so these tests also
   stand guard over its absence: no word spans, and no key that moves a cursor. */

import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { CalibrationFlow, savedMessage } from '../static/js/calibration/flow.js';
import { extensionFor } from '../static/js/calibration/api.js';

const PASSAGE = [
  { id: 's01', text: 'The sun rose over the quiet harbor.' },
  { id: 's02', text: 'A pot of fresh coffee smells good.' },
];

let dom, flow, api, recorder, saves;

class FakeRecorder {
  constructor() { this.started = null; this.cancelled = false; }
  start(onStarted) {
    onStarted();
    return new Promise(resolve => { this.finish = resolve; });
  }
  stop() { this.finish?.({ blob: new Blob(['audio']), duration: 2000, mimeType: 'audio/webm' }); }
  cancel() { this.cancelled = true; this.finish?.(null); }
}

const record = (id, placed, total) => ({
  id, text: 'x', recorded_at: 'now', duration_sec: 2.0, transcript: 't',
  placed_count: placed, word_count: total, words: [],
});

beforeEach(() => {
  dom = new JSDOM(readFileSync(new URL('../static/calibrate.html', import.meta.url)),
    { url: 'http://localhost/' });
  globalThis.document = dom.window.document;
  globalThis.Element = dom.window.Element;
  globalThis.Blob = dom.window.Blob;
  saves = [];
  api = {
    list: async () => [],
    save: async payload => { saves.push(payload); return record(payload.id, 7, 7); },
    audioUrl: id => `/api/calibration/sentences/${id}/audio`,
  };
  recorder = new FakeRecorder();
  flow = new CalibrationFlow({
    doc: dom.window.document, passage: PASSAGE, api,
    makeRecorder: () => recorder,
    makeMonitor: () => ({ start: async () => {}, stop() {} }),
    autoAdvanceMs: 5000,
  }).attach();
});

afterEach(() => {
  clearTimeout(flow.autoTimer);
  dom.window.close();
});

const $ = id => dom.window.document.getElementById(id);

test('the page opens on the intro, not on a recording', () => {
  assert.equal($('screen-intro').hidden, false);
  assert.equal($('screen-read').hidden, true);
  assert.equal(flow.state, 'ready');
});

test('the mic check only unlocks Start reading once it has heard something', async () => {
  await flow.beginMicCheck();
  assert.equal($('screen-mic').hidden, false);
  assert.equal($('mic-continue').disabled, true);
  flow.onLevel(0.4, true, 400);
  assert.equal($('mic-continue').disabled, true);   // 400ms is not enough yet
  flow.onLevel(0.4, true, 400);
  assert.equal($('mic-continue').disabled, false);
  assert.match($('mic-status').textContent, /we can hear you/i);
});

test('a card shows the sentence as one piece, with no per-word markup', () => {
  flow.startSentence(0);
  assert.equal($('sentence').textContent, PASSAGE[0].text);
  assert.equal($('sentence').querySelectorAll('span').length, 0);
  assert.equal($('read-count').textContent, 'Sentence 1 of 2');
});

test('recording starts on the button and ends on "I\'ve read it"', async () => {
  flow.startSentence(0);
  assert.equal($('record-btn').hidden, false);
  assert.equal($('done-btn').hidden, true);

  const recording = flow.startRecording();
  assert.equal(flow.state, 'recording');
  assert.equal($('record-btn').hidden, true);
  assert.equal($('done-btn').hidden, false);
  assert.equal($('rec-dot').hidden, false);

  flow.finishReading();
  await recording;
  assert.deepEqual(saves.map(s => [s.id, s.text]), [['s01', PASSAGE[0].text]]);
  assert.equal(flow.state, 'saved');
  assert.equal($('rec-label').textContent, 'Saved.');
  assert.equal($('rec-dot').hidden, true);
});

test('arrow keys and taps on the sentence do nothing at all', async () => {
  flow.startSentence(0);
  const before = $('sentence').outerHTML;
  for (const key of ['ArrowRight', 'ArrowLeft']) {
    dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key, bubbles: true }));
  }
  $('sentence').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
  assert.equal($('sentence').outerHTML, before);
  assert.equal(flow.state, 'ready');
});

test('space starts the recording, then finishes it', async () => {
  flow.startSentence(0);
  const press = () => dom.window.document.dispatchEvent(
    new dom.window.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
  press();
  assert.equal(flow.state, 'recording');
  press();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(flow.state, 'saved');
});

test('a failed save keeps the card where it is and says so', async () => {
  api.save = async () => { throw new Error('ffmpeg is not installed'); };
  flow.startSentence(0);
  const recording = flow.startRecording();
  flow.finishReading();
  await recording;
  assert.equal(flow.state, 'ready');
  assert.equal($('rec-label').textContent, 'ffmpeg is not installed');
  assert.equal($('record-btn').hidden, false);     // ready to try again
  assert.equal(flow.cursor, 0);                    // never pushed past it
});

test('skipping moves on without saving, mid-recording or before it', async () => {
  flow.startSentence(0);
  flow.skipSentence();
  assert.equal(flow.cursor, 1);
  assert.equal(saves.length, 0);

  const recording = flow.startRecording();
  flow.skipSentence();                  // half way through a take
  await recording;
  assert.equal(recorder.cancelled, true);
  assert.equal(saves.length, 0);
});

test('pausing cancels the take and offers Resume', async () => {
  flow.startSentence(0);
  const recording = flow.startRecording();
  flow.pauseReading();
  await recording;
  assert.equal($('paused-panel').hidden, false);
  assert.equal(recorder.cancelled, true);
  assert.equal(saves.length, 0);
});

test('the finish screen counts the sentences recorded', async () => {
  flow.saved.set('s01', record('s01', 7, 7));
  flow.saved.set('s02', record('s02', 4, 7));
  flow.buildFinish();
  assert.equal($('screen-finish').hidden, false);
  assert.equal($('finish-summary').textContent, '2 of 2 sentences recorded.');
  const rows = $('finish-list').querySelectorAll('li');
  assert.equal(rows.length, 2);
  assert.match(rows[1].textContent, /Recorded · 2\.0s/);
  // Play and Re-record on a recorded sentence; Record on one that has none.
  assert.deepEqual([...rows[0].querySelectorAll('button')].map(b => b.textContent),
    ['Play', 'Re-record']);
  flow.saved.delete('s02');
  flow.buildFinish();
  assert.deepEqual([...$('finish-list').querySelectorAll('li')[1]
    .querySelectorAll('button')].map(b => b.textContent), ['Record']);
});

test('re-recording one sentence from the finish list returns to the finish list', async () => {
  flow.saved.set('s01', record('s01', 7, 7));
  flow.buildFinish();
  $('finish-list').querySelectorAll('li')[1].querySelector('button').click();
  assert.equal(flow.cursor, 1);
  assert.equal($('screen-read').hidden, false);
  const recording = flow.startRecording();
  flow.finishReading();
  await recording;
  flow.advance();
  assert.equal($('screen-finish').hidden, false);
});

test('what the reader is told never grades the take', () => {
  for (const record of [{ placed_count: 7, word_count: 7 }, { placed_count: 0, word_count: 7 }, undefined]) {
    assert.equal(savedMessage(record), 'Saved.');
  }
});

test('the upload is named for what the browser actually recorded', () => {
  assert.equal(extensionFor('audio/webm;codecs=opus'), 'webm');
  assert.equal(extensionFor('audio/mp4'), 'm4a');      // iPhone Safari
  assert.equal(extensionFor(''), 'webm');
});

/* ---- two takes per phrase (enrollment) ---------------------------------- */

function twoTakeFlow(options = {}) {
  clearTimeout(flow.autoTimer);
  return new CalibrationFlow({
    doc: dom.window.document, passage: PASSAGE, api, takes: 2,
    makeRecorder: () => recorder,
    makeMonitor: () => ({ start: async () => {}, stop() {} }),
    autoAdvanceMs: 5000,
    ...options,
  });
}

async function recordCard(f) {
  const recording = f.startRecording();
  f.finishReading();
  await recording;
}

test('each phrase is asked for twice, and each take is saved with its number', async () => {
  const f = twoTakeFlow();
  f.startSentence(0);
  assert.equal($('read-count').textContent, 'Phrase 1 of 2 · take 1 of 2');
  await recordCard(f);
  f.advance();
  assert.equal($('read-count').textContent, 'Phrase 1 of 2 · take 2 of 2');
  assert.equal($('sentence').textContent, PASSAGE[0].text);
  assert.match($('rec-label').textContent, /Once more/);
  await recordCard(f);
  f.advance();
  assert.equal($('read-count').textContent, 'Phrase 2 of 2 · take 1 of 2');
  assert.deepEqual(saves.map(s => [s.id, s.take]), [['s01', 1], ['s01', 2]]);
  clearTimeout(f.autoTimer);
});

test('a phrase dot fills only when both takes are in', async () => {
  const f = twoTakeFlow();
  f.startSentence(0);
  await recordCard(f);
  assert.doesNotMatch($('read-dots').innerHTML, /class="done/);
  f.advance();
  await recordCard(f);
  assert.match($('read-dots').innerHTML, /<i class="done now"><\/i>/);
  clearTimeout(f.autoTimer);
});

test('starting again resumes at the first missing take', async () => {
  api.list = async () => [{ ...record('s01', 7, 7), take: 1 }, { ...record('s01', 7, 7), take: 2 },
    { ...record('s02', 7, 7), take: 1 }];
  const f = twoTakeFlow();
  await f.startReading();
  assert.equal($('read-count').textContent, 'Phrase 2 of 2 · take 2 of 2');
});

test('the finish list counts takes, and re-recording does both takes then returns', async () => {
  const f = twoTakeFlow();
  f.saved.set('s01', { ...record('s01', 7, 7), take: 1 });
  f.saved.set('s01#2', { ...record('s01', 6, 7), take: 2 });
  f.saved.set('s02', { ...record('s02', 7, 7), take: 1 });
  f.buildFinish();
  assert.equal($('finish-summary').textContent, '1 of 2 phrases recorded twice.');
  const rows = $('finish-list').querySelectorAll('li');
  assert.match(rows[0].textContent, /2 of 2 takes/);
  assert.match(rows[1].textContent, /1 of 2 takes/);
  rows[1].querySelector('button:last-of-type').click();      // Re-record
  assert.equal($('read-count').textContent, 'Phrase 2 of 2 · take 1 of 2');
  await recordCard(f); f.advance();
  assert.equal($('read-count').textContent, 'Phrase 2 of 2 · take 2 of 2');
  await recordCard(f); f.advance();
  assert.equal($('screen-finish').hidden, false);
  clearTimeout(f.autoTimer);
});

test('Play uses the latest take that exists', () => {
  const urls = [];
  globalThis.Audio = class { pause() {} play() { urls.push(this.src); return Promise.resolve(); } };
  const f = twoTakeFlow();
  api.audioUrl = (id, take) => `${id}@${take}`;
  f.saved.set('s01', { ...record('s01', 7, 7), take: 1 });
  f.saved.set('s01#2', { ...record('s01', 7, 7), take: 2 });
  f.saved.set('s02', { ...record('s02', 7, 7), take: 1 });
  f.buildFinish();
  const rows = $('finish-list').querySelectorAll('li');
  rows[0].querySelector('button').click();
  rows[1].querySelector('button').click();
  assert.deepEqual(urls, ['s01@2', 's02@1']);
  delete globalThis.Audio;
});
