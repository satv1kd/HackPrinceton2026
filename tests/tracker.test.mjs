// Strategy layering in the calibration ProgressTracker:
// ASR leads, voice-activity pacing takes over when ASR goes quiet, manual
// control always wins — and nothing ever counts as a mistake.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ProgressTracker, ASR_STALE_MS } from '../static/js/calibration/tracker.js';

const WORDS = 'The sun rose over the quiet harbor.'.split(' ');

function make(pace = 2) {
  let t = 0;
  const clock = () => t;
  const events = [];
  const tracker = new ProgressTracker(WORDS, {
    now: clock,
    pace,
    onProgress: (index, source) => events.push([index, source]),
    onComplete: () => events.push(['complete']),
  });
  tracker.start();
  return { tracker, events, tick: ms => { t += ms; } };
}

test('ASR transcripts advance the caret and timestamp each word', () => {
  const { tracker, events, tick } = make();
  tick(500);
  tracker.handleTranscript('the sun');
  assert.equal(tracker.index, 2);
  assert.deepEqual(events, [[2, 'asr']]);
  assert.deepEqual(tracker.timestamps.map(t => [t.word, t.atMs, t.source]),
    [['The', 500, 'asr'], ['sun', 500, 'asr']]);
});

test('voice activity only takes over after ASR has been stale for 1.5s', () => {
  const { tracker, tick } = make(2); // 2 syl/sec → a 1-syllable word needs 500ms of speech
  tracker.handleTranscript('the sun'); // index 2, resets the stale clock
  // Speech continues but ASR hears nothing more.
  let advancedDuringQuietPeriod = false;
  for (let i = 0; i < 18; i++) { // 18 × 80ms ≈ 1.44s, still inside the stale window
    tick(80);
    tracker.voiceTick(true, 80);
    if (tracker.index > 2) advancedDuringQuietPeriod = true;
  }
  assert.equal(advancedDuringQuietPeriod, false, 'voice must wait out the stale window');
  // Past the window, voiced time starts banking toward the next word.
  for (let i = 0; i < 10 && tracker.index === 2; i++) { tick(80); tracker.voiceTick(true, 80); }
  assert.equal(tracker.index, 3);
  assert.equal(tracker.timestamps.at(-1).source, 'voice');
});

test('silence never advances the caret', () => {
  const { tracker, tick } = make();
  tick(ASR_STALE_MS + 1000);
  for (let i = 0; i < 50; i++) tracker.voiceTick(false, 80);
  assert.equal(tracker.index, 0);
});

test('manual control can move backward and truncates stale word marks', () => {
  const { tracker } = make();
  tracker.handleTranscript('the sun rose over');
  assert.equal(tracker.index, 4);
  tracker.moveTo(2); // the reader wants to redo "rose over"
  assert.equal(tracker.index, 2);
  assert.deepEqual(tracker.timestamps.map(t => t.word), ['The', 'sun']);
  tracker.next();
  assert.equal(tracker.index, 3);
  assert.equal(tracker.timestamps.at(-1).source, 'manual');
});

test('finishing the last word fires onComplete exactly once', () => {
  const { tracker, events } = make();
  tracker.handleTranscript('the sun rose over the quiet harbor');
  assert.equal(tracker.completed, true);
  tracker.handleTranscript('the sun rose over the quiet harbor harbor');
  tracker.next();
  assert.equal(events.filter(e => e[0] === 'complete').length, 1);
});

test('stale ASR text can never pull progress backward', () => {
  const { tracker } = make();
  tracker.moveTo(5);
  tracker.handleTranscript('the sun');
  assert.equal(tracker.index, 5);
});
