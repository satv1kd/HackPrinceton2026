/**
 * Tests for the client modules added by tasks 9, 10 and 11.
 *
 * Run with:  node --test tests/client-ui.test.mjs
 *
 * Dependency-free: uses tests/support/dom-stub.mjs rather than jsdom, so the
 * suite runs on a clean checkout with no npm install.
 */

import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';

import { FakeAudio, installDOM, resetDOM } from './support/dom-stub.mjs';

installDOM();

const { CorrectionFlow, labelFor } = await import('../static/js/correction.js');
const { TTSPlayer } = await import('../static/js/tts.js');
const { SampleRecorderFlow, SetupMode, extensionFor } = await import('../static/js/setup.js');

let fetchCalls;

beforeEach(() => {
  resetDOM();
  installDOM();
  fetchCalls = [];
  globalThis.fetch = async () => {
    throw new Error('fetch not stubbed for this test');
  };
});

function stubFetch(routes) {
  globalThis.fetch = async (url, options = {}) => {
    fetchCalls.push({ url: String(url), options });
    for (const [pattern, reply] of Object.entries(routes)) {
      if (String(url).includes(pattern)) return reply(options);
    }
    return jsonReply(404, { detail: 'not stubbed' });
  };
}

function jsonReply(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    blob: async () => new Blob([]),
  };
}

/* ========================================================== correction.js */

test('labelFor maps candidates to feedback labels', () => {
  assert.deepEqual(
    labelFor({ type: 'phrase', phrase_id: 'p1', text: 'water' }),
    { type: 'phrase', phrase_id: 'p1' },
  );
  assert.deepEqual(
    labelFor({ type: 'generated', text: 'a new sentence' }),
    { type: 'text', text: 'a new sentence' },
  );
  // A phrase candidate with no id cannot be a phrase label.
  assert.deepEqual(labelFor({ type: 'phrase', text: 'x' }), { type: 'text', text: 'x' });
  assert.equal(labelFor(null), null);
});

test('picking a phrase from the grid resolves to a phrase label', async () => {
  const flow = new CorrectionFlow({
    loadPhrases: async () => [{ id: 'p1', text: 'I want water', emoji: '💧' }],
  });
  const pending = flow.open();
  await Promise.resolve();
  await Promise.resolve();

  const button = document.querySelector('.phrase-grid-btn');
  assert.ok(button, 'the grid should render a button per phrase');
  await button.click();

  assert.deepEqual(await pending, { type: 'phrase', phrase_id: 'p1' });
  assert.equal(document.querySelector('.correction-overlay'), null, 'overlay should close');
});

test('Skip resolves to null, backing out resolves to undefined', async () => {
  // These are different: null is a stored negative signal, undefined stores
  // nothing at all.
  const skip = new CorrectionFlow({ loadPhrases: async () => [] });
  const skipPending = skip.open();
  await skip.overlay.querySelector('.correction-skip-btn').click();
  assert.equal(await skipPending, null);

  const back = new CorrectionFlow({ loadPhrases: async () => [] });
  const backPending = back.open();
  await back.overlay.querySelector('.correction-cancel-btn').click();
  assert.equal(await backPending, undefined);
});

test('Escape backs out without storing anything', async () => {
  const flow = new CorrectionFlow({ loadPhrases: async () => [] });
  const pending = flow.open();
  await flow.overlay.keydown('Escape');
  assert.equal(await pending, undefined);
});

test('typing a correction resolves to a text label and rejects blanks', async () => {
  const flow = new CorrectionFlow({ loadPhrases: async () => [] });
  const pending = flow.open();
  const form = flow.overlay.querySelector('.correction-type');
  const input = flow.overlay.querySelector('INPUT');

  input.value = '   ';
  await form.submit();
  assert.ok(flow.overlay, 'a blank entry must not close the overlay');

  input.value = '  table for two  ';
  await form.submit();
  assert.deepEqual(await pending, { type: 'text', text: 'table for two' });
});

test('other candidates are offered again', async () => {
  const flow = new CorrectionFlow({ loadPhrases: async () => [] });
  const pending = flow.open({
    otherCandidates: [{ type: 'phrase', phrase_id: 'p2', text: 'I want tea' }],
  });
  await flow.overlay.querySelector('.correction-btn').click();
  assert.deepEqual(await pending, { type: 'phrase', phrase_id: 'p2' });
});

test('phrase text is set as text, never parsed as markup', async () => {
  // Phrase text is user input and goes straight into the DOM.
  const nasty = '<img src=x onerror=alert(1)>';
  const flow = new CorrectionFlow({ loadPhrases: async () => [{ id: 'p1', text: nasty }] });
  flow.open();
  await Promise.resolve();
  await Promise.resolve();

  const label = flow.overlay.querySelector('.text');
  assert.equal(label.textContent, nasty);
  assert.equal(flow.overlay.querySelectorAll('IMG').length, 0, 'no element may be created from it');
});

test('a failing phrase load shows the error instead of an empty grid', async () => {
  const flow = new CorrectionFlow({
    loadPhrases: async () => {
      throw new Error('Could not reach Heard.');
    },
  });
  flow.open();
  await Promise.resolve();
  await Promise.resolve();
  assert.match(flow.overlay.querySelector('.grid-note').textContent, /Could not reach Heard/);
});

test('an empty phrase library says so', async () => {
  const flow = new CorrectionFlow({ loadPhrases: async () => [] });
  flow.open();
  await Promise.resolve();
  await Promise.resolve();
  assert.match(flow.overlay.querySelector('.grid-note').textContent, /No saved phrases yet/);
});

/* ================================================================= tts.js */

function stubSpeechSynthesis() {
  const spoken = [];
  globalThis.speechSynthesis = {
    speak: utterance => spoken.push(utterance),
    cancel: () => {},
  };
  globalThis.SpeechSynthesisUtterance = class {
    constructor(text) {
      this.text = text;
      this.rate = 1;
      this.pitch = 1;
    }
  };
  return spoken;
}

test('a 503 from the server falls back to the browser voice', async () => {
  // 503 is the documented signal, not a fault.
  const spoken = stubSpeechSynthesis();
  stubFetch({ '/api/tts': () => jsonReply(503, { detail: 'unavailable' }) });

  assert.equal(await new TTSPlayer().speak('I want water'), 'browser');
  assert.equal(spoken[0].text, 'I want water');
  assert.equal(spoken[0].rate, 0.9, 'slowed slightly so a listener catches it first time');
});

test('audio from the server is played', async () => {
  stubFetch({
    '/api/tts': () => ({ ok: true, status: 200, blob: async () => new Blob([new Uint8Array(1024)]) }),
  });
  const player = new TTSPlayer();
  assert.equal(await player.speak('hello'), 'elevenlabs');
  assert.equal(player.audio.played, true);
});

test('an empty body falls back rather than playing silence', async () => {
  const spoken = stubSpeechSynthesis();
  stubFetch({ '/api/tts': () => ({ ok: true, status: 200, blob: async () => new Blob([]) }) });
  assert.equal(await new TTSPlayer().speak('hello'), 'browser');
  assert.equal(spoken.length, 1);
});

test('a network failure falls back', async () => {
  stubSpeechSynthesis();
  globalThis.fetch = async () => {
    throw new TypeError('Failed to fetch');
  };
  assert.equal(await new TTSPlayer().speak('hello'), 'browser');
});

test('with no voice at all, speaking is silent rather than an error', async () => {
  stubFetch({ '/api/tts': () => jsonReply(503, {}) });
  assert.equal(await new TTSPlayer().speak('hello'), 'silent');
});

test('blank text is not spoken and makes no request', async () => {
  stubFetch({ '/api/tts': () => jsonReply(200, {}) });
  assert.equal(await new TTSPlayer().speak('   '), 'silent');
  assert.equal(fetchCalls.length, 0);
});

test('the text is URL-encoded into the query', async () => {
  stubFetch({ '/api/tts': () => ({ ok: true, status: 200, blob: async () => new Blob([new Uint8Array(8)]) }) });
  await new TTSPlayer().speak('Café ☕ & more');
  assert.match(fetchCalls[0].url, /text=Caf%C3%A9/);
  assert.match(fetchCalls[0].url, /%26/, '& must be encoded, not read as a separator');
});

/* =============================================================== setup.js */

test('extensionFor maps the browser recording formats', () => {
  assert.equal(extensionFor('audio/webm;codecs=opus'), 'webm');
  assert.equal(extensionFor('audio/mp4'), 'm4a');
  assert.equal(extensionFor('audio/ogg'), 'ogg');
  assert.equal(extensionFor(''), 'bin');
});

function setupMode() {
  return new SetupMode(null, {
    container: document.createElement('div'),
    statusEl: document.createElement('p'),
    editorEl: document.createElement('div'),
  });
}

test('phrase cards show the recording count and the personal-word badge', async () => {
  stubFetch({
    '/api/phrases': () =>
      jsonReply(200, [
        { id: 'p1', text: 'I want water', emoji: '💧', sample_count: 3 },
        { id: 'p2', text: 'wawa', sample_count: 1, personal_word: true },
      ]),
  });
  const setup = setupMode();
  await setup.load();

  const cards = setup.list.querySelectorAll('.phrase-card');
  assert.equal(cards.length, 2);
  assert.equal(cards[0].querySelector('.sample-count').textContent, '3 of 3 recordings');
  assert.ok(cards[0].querySelector('.sample-count').classList.contains('done'));
  assert.equal(cards[1].querySelector('.sample-count').textContent, '1 of 3 recordings');
  assert.equal(cards[1].querySelector('.badge').textContent, 'My own word');
  assert.equal(cards[0].querySelector('.badge'), null);
});

test('an empty library prompts for the first phrase', async () => {
  stubFetch({ '/api/phrases': () => jsonReply(200, []) });
  const setup = setupMode();
  await setup.load();
  assert.match(setup.status.textContent, /Add your first one/);
});

test('phrase text in a card is text, not markup', async () => {
  stubFetch({ '/api/phrases': () => jsonReply(200, [{ id: 'p1', text: '<b>hi</b>' }]) });
  const setup = setupMode();
  await setup.load();
  assert.equal(setup.list.querySelector('.phrase-text').textContent, '<b>hi</b>');
  assert.equal(setup.list.querySelectorAll('B').length, 0);
});

test('a photo is shown instead of an emoji when present', async () => {
  stubFetch({
    '/api/phrases': () =>
      jsonReply(200, [{ id: 'p1', text: 'bus', image_url: '/api/phrases/p1/image' }]),
  });
  const setup = setupMode();
  await setup.load();
  assert.equal(setup.list.querySelector('.phrase-icon IMG').src, '/api/phrases/p1/image');
});

test('creating a phrase posts text, emoji and the personal-word flag', async () => {
  stubFetch({
    '/api/phrases': options =>
      options.method === 'POST'
        ? jsonReply(201, { id: 'new', text: 'I want water', sample_count: 0 })
        : jsonReply(200, []),
  });
  const setup = setupMode();
  await setup.load();

  setup.editor.querySelector('INPUT').value = '  I want water  ';
  await setup.editor.querySelectorAll('.emoji-option')[0].click();
  setup.editor.querySelector('.checkbox-row INPUT').checked = true;
  await setup.editor.querySelector('.phrase-form').submit();

  const post = fetchCalls.find(call => call.options.method === 'POST');
  assert.ok(post, 'the form should POST');
  const body = post.options.body;
  assert.equal(body.get('text'), 'I want water', 'text is trimmed');
  assert.equal(body.get('personal_word'), 'true');
  assert.ok(body.get('emoji'), 'the chosen emoji is sent');
  setup.cancel();
});

test('an empty phrase is refused before any request', async () => {
  stubFetch({ '/api/phrases': () => jsonReply(200, []) });
  const setup = setupMode();
  await setup.load();
  const before = fetchCalls.length;

  setup.editor.querySelector('INPUT').value = '   ';
  await setup.editor.querySelector('.phrase-form').submit();

  assert.equal(fetchCalls.length, before, 'no request for an empty phrase');
  assert.match(setup.editor.querySelector('.form-error').textContent, /type the words/);
});

test('selecting an emoji twice clears it', async () => {
  stubFetch({ '/api/phrases': () => jsonReply(200, []) });
  const setup = setupMode();
  await setup.load();
  const option = setup.editor.querySelectorAll('.emoji-option')[0];

  await option.click();
  assert.equal(option.getAttribute('aria-checked'), 'true');
  await option.click();
  assert.equal(option.getAttribute('aria-checked'), 'false');
});

/* --- sample recorder flow --- */

function fakeRecorder(recordings) {
  const queue = [...recordings];
  return {
    session: null,
    async start() {
      return queue.shift() ?? null;
    },
    stop() {},
    cancel() {},
  };
}

const RECORDING = {
  // A real Blob: FormData.append and URL.createObjectURL both demand one.
  blob: new Blob([new Uint8Array(2048)], { type: 'audio/webm' }),
  mimeType: 'audio/webm;codecs=opus',
  duration: 900,
};

test('each kept sample uploads immediately', async () => {
  // Uploading per sample rather than all three at the end means a dropped
  // connection cannot lose the whole set.
  stubFetch({ '/samples': () => jsonReply(201, { id: 's1' }) });
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'I want water', sample_count: 0 },
    { recorder: fakeRecorder([RECORDING]), onDone: () => {} },
  );
  flow.open();

  await flow.recordBtn.click();
  assert.equal(flow.controls.hidden, false, 'playback controls appear after recording');
  await flow.keepBtn.click();

  const post = fetchCalls.find(call => call.url.includes('/samples'));
  assert.ok(post);
  assert.equal(post.url, '/api/phrases/p1/samples');
  assert.equal(flow.recorded, 1);
  flow.close();
});

test('progress counts up to three and finishes', async () => {
  stubFetch({ '/samples': () => jsonReply(201, { id: 's' }) });
  let done = false;
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'x', sample_count: 2 },
    { recorder: fakeRecorder([RECORDING]), onDone: () => { done = true; } },
  );
  flow.open();
  assert.match(flow.progress.textContent, /Recording 3 of 3/);

  await flow.recordBtn.click();
  await flow.keepBtn.click();
  assert.equal(done, true, 'the third sample completes the set');
});

test('re-recording discards the pending take without uploading', async () => {
  stubFetch({ '/samples': () => jsonReply(201, { id: 's' }) });
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'x', sample_count: 0 },
    { recorder: fakeRecorder([RECORDING, RECORDING]), onDone: () => {} },
  );
  flow.open();

  await flow.recordBtn.click();
  await flow.retryBtn.click();
  assert.equal(flow.pending, null);
  assert.equal(flow.controls.hidden, true);
  assert.equal(fetchCalls.filter(call => call.url.includes('/samples')).length, 0);
  flow.close();
});

test('a too-short recording asks for another, and does not upload', async () => {
  stubFetch({ '/samples': () => jsonReply(201, { id: 's' }) });
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'x', sample_count: 0 },
    { recorder: fakeRecorder([null]), onDone: () => {} },
  );
  flow.open();
  await flow.recordBtn.click();

  assert.match(flow.message.textContent, /too short/);
  assert.equal(flow.controls.hidden, true);
  flow.close();
});

test('a rejected upload keeps the take so it can be retried', async () => {
  stubFetch({
    '/samples': () => jsonReply(422, { detail: { message: "Didn't catch that" } }),
  });
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'x', sample_count: 0 },
    { recorder: fakeRecorder([RECORDING]), onDone: () => {} },
  );
  flow.open();

  await flow.recordBtn.click();
  await flow.keepBtn.click();

  assert.equal(flow.recorded, 0);
  assert.ok(flow.pending, 'the recording is not thrown away on a failed upload');
  assert.equal(flow.keepBtn.disabled, false, 'and Keep can be pressed again');
  flow.close();
});

test('playback uses the pending blob', async () => {
  stubFetch({ '/samples': () => jsonReply(201, { id: 's' }) });
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'x', sample_count: 0 },
    { recorder: fakeRecorder([RECORDING]), onDone: () => {} },
  );
  flow.open();
  await flow.recordBtn.click();
  await flow.playBtn.click();

  assert.ok(FakeAudio.created.some(audio => audio.played), 'something should play');
  flow.close();
});

test('instructions are not styled as errors, but failures are', async () => {
  // Caught in a browser, not here: the message slot was always given the
  // error class, so "Tap to record, then tap again to stop" — an
  // instruction to someone who has done nothing wrong — was shown in red.
  stubFetch({ '/samples': () => jsonReply(500, { detail: 'nope' }) });
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'I want water', sample_count: 0 },
    { recorder: fakeRecorder([RECORDING]), onDone: () => {} },
  );
  flow.open();

  assert.match(flow.message.textContent, /Tap to record/);
  assert.equal(flow.message.className, 'form-note');
  assert.equal(flow.message.getAttribute('role'), 'status');

  await flow.recordBtn.click();
  assert.equal(flow.message.className, 'form-note', 'playback prompt is not a failure');

  await flow.keepBtn.click();
  assert.equal(flow.message.className, 'form-error', 'a failed upload is');
  flow.close();
});

test('closing the flow removes the overlay', async () => {
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'x', sample_count: 0 },
    { recorder: fakeRecorder([]), onDone: () => {} },
  );
  flow.open();
  assert.ok(document.querySelector('.sample-flow-overlay'));
  flow.close();
  assert.equal(document.querySelector('.sample-flow-overlay'), null);
});

// -- playback fallback (the <audio> element refusing a clip) ----------------

class FakeAudioContext {
  static decodes = 'ok';
  static instances = [];
  constructor() { this.state = 'suspended'; this.started = []; this.closed = false; FakeAudioContext.instances.push(this); }
  async resume() { this.state = 'running'; }
  async close() { this.closed = true; }
  async decodeAudioData() {
    if (FakeAudioContext.decodes !== 'ok') throw new Error('EncodingError');
    return { duration: 1.2 };
  }
  createBufferSource() {
    const ctx = this;
    return { connect() {}, start() { ctx.started.push(this); }, stop() {} };
  }
  get destination() { return {}; }
}

function refusingAudio(t) {
  const original = FakeAudio.prototype.play;
  FakeAudio.prototype.play = async function () {
    const error = new Error('Failed to load because no supported source was found.');
    error.name = 'NotSupportedError';
    throw error;
  };
  t.after(() => { FakeAudio.prototype.play = original; delete globalThis.AudioContext; FakeAudioContext.decodes = 'ok'; });
  globalThis.AudioContext = FakeAudioContext;
}

async function recordedFlow() {
  stubFetch({ '/samples': () => jsonReply(201, { id: 's' }) });
  const flow = new SampleRecorderFlow(
    { id: 'p1', text: 'x', sample_count: 0 },
    { recorder: fakeRecorder([{ ...RECORDING, blob: { size: 2048, arrayBuffer: async () => new ArrayBuffer(8) } }]), onDone: () => {} },
  );
  flow.open();
  await flow.recordBtn.click();
  return flow;
}

const settle = () => new Promise(resolve => setTimeout(resolve, 0));

test('a clip the <audio> element refuses is played through Web Audio instead', async t => {
  refusingAudio(t);
  const flow = await recordedFlow();
  await flow.playBtn.click();
  await settle();
  const context = FakeAudioContext.instances.at(-1);
  assert.equal(context.state, 'running', 'context resumed inside the tap');
  assert.equal(context.started.length, 1, 'decoded clip played');
  assert.notEqual(flow.message.className, 'form-error', 'no error shown when the fallback works');
  flow.close();
  assert.equal(context.closed, true, 'closing the screen releases the audio context');
});

test('a clip neither player can decode gets plain advice, not a browser error', async t => {
  refusingAudio(t);
  FakeAudioContext.decodes = 'fail';
  const originalError = console.error;
  console.error = () => {};
  t.after(() => { console.error = originalError; });
  const flow = await recordedFlow();
  await flow.playBtn.click();
  await settle();
  assert.equal(flow.message.className, 'form-error');
  assert.match(flow.message.textContent, /No sound was captured/);
  assert.doesNotMatch(flow.message.textContent, /NotSupportedError/);
  flow.close();
});
