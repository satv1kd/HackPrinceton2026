// Task 32: the "Your sounds" coverage screen.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

let dom, SoundCoverage, label;

const sound = (phoneme, example, ipa, vowel, examples, status, pending = 0) =>
  ({ phoneme, example, ipa, vowel, examples, status, pending, self_probability: 0.5 });

function profile(overrides = {}) {
  return {
    ready: true, reason: null, version: 3, sources: { take: 36, feedback: 4 }, min_examples: 2,
    retrain_after: 10, last_retrain: null,
    sounds: [
      sound('R', 'red', 'ɹ', false, 6, 'learned', 4),
      sound('W', 'water', 'w', false, 8, 'learned'),
      sound('ZH', 'garage', 'ʒ', false, 1, 'needs_more'),
      sound('IY', 'see', 'i', true, 0, 'not_yet'),
    ],
    patterns: [
      { intended: 'R', heard: 'W', probability: 0.7, self: 0.2, examples: 6 },
      { intended: 'ZH', heard: '<DEL>', probability: 0.4, self: 0.3, examples: 1 },
      { intended: 'W', heard: 'R', probability: 0.1, self: 0.9, examples: 8 },   // too weak to mention
    ],
    ...overrides,
  };
}

const phrases = [
  { id: 'open-the-garage', text: 'Open the garage', phonemes: ['OW', 'P', 'AH', 'N', 'DH', 'AH', 'G', 'ER', 'AA', 'ZH'] },
  { id: 'read-me-a-story', text: 'Read me a story', phonemes: ['R', 'IY', 'D'] },
  { id: 'wait-for-me', text: 'Wait for me', phonemes: ['W', 'EY', 'T'] },
];

beforeEach(async () => {
  dom = new JSDOM('<!doctype html><section id="c" hidden></section>', { url: 'http://localhost/' });
  globalThis.document = dom.window.document;
  globalThis.CustomEvent = dom.window.CustomEvent;
  ({ SoundCoverage, label } = await import('../static/js/coverage.js'));
});

afterEach(() => dom.window.close());

const box = () => document.getElementById('c');

function make(responses) {
  const calls = [];
  const request = async url => {
    calls.push(url);
    const value = responses[url];
    if (value instanceof Error) throw value;
    return typeof value === 'function' ? value() : value;
  };
  return { coverage: new SoundCoverage(box(), { request }), calls };
}

test('tiles show the example word, symbol and status, grouped', async () => {
  const { coverage } = make({ '/api/profile': profile(), '/api/calibration/enrollment-set': { phrases } });
  await coverage.load();
  assert.equal(box().hidden, false);
  const groups = [...box().querySelectorAll('.coverage-group h3')].map(h => h.textContent);
  assert.deepEqual(groups, ['Consonants', 'Vowels']);
  const tiles = [...box().querySelectorAll('.coverage-tile')];
  assert.equal(tiles.length, 4);
  assert.match(tiles[0].textContent, /red\/ɹ\/Learned/);
  assert.equal(tiles[0].getAttribute('aria-label'), 'The r sound in “red”: Learned, 6 examples, 4 new since the last update');
  assert.ok(tiles[0].querySelector('.coverage-progress'));
  assert.match(tiles[3].className, /not_yet/);
});

test('summary counts what was learned and the next update', async () => {
  const { coverage } = make({ '/api/profile': profile(), '/api/calibration/enrollment-set': { phrases } });
  await coverage.load();
  const summary = box().querySelector('.coverage-summary').textContent;
  assert.match(summary, /2 of 4 sounds learned/);
  assert.match(summary, /36 calibration recordings and 4 confirmed answers/);
  assert.match(summary, /closest: the r sound in “red”, 4 of 10/);
});

test('patterns are described as what Heard listens for, never as mistakes', async () => {
  const { coverage } = make({ '/api/profile': profile(), '/api/calibration/enrollment-set': { phrases } });
  await coverage.load();
  const items = [...box().querySelectorAll('.coverage-patterns li')].map(li => li.textContent);
  assert.deepEqual(items, [
    'When you say the r sound in “red”, it often comes through like the w sound in “water” — Heard now listens for that.',
    'When you say the zh sound in “garage”, it is often soft or quiet — Heard now listens for that.',
  ]);
  for (const text of items) assert.doesNotMatch(text, /wrong|error|mistake|incorrect/i);
});

test('weak sounds point at the calibration phrases that contain them', async () => {
  const { coverage } = make({ '/api/profile': profile(), '/api/calibration/enrollment-set': { phrases } });
  await coverage.load();
  const suggest = box().querySelector('.coverage-suggest');
  assert.match(suggest.textContent, /“Open the garage”, “Read me a story”/);
  assert.doesNotMatch(suggest.textContent, /Wait for me/);
  assert.equal(suggest.querySelector('a').getAttribute('href'), '/calibrate');
});

test('a not-ready reason and a rolled-back update are explained', async () => {
  const { coverage } = make({
    '/api/profile': profile({ ready: false, reason: 'Record the 18 calibration phrases first.', version: null,
      last_retrain: { promoted: false } }),
    '/api/calibration/enrollment-set': new Error('offline'),
  });
  await coverage.load();
  const summary = box().querySelector('.coverage-summary').textContent;
  assert.match(summary, /Record the 18 calibration phrases first\./);
  assert.match(summary, /kept the version before it/);
  assert.match(box().querySelector('.coverage-suggest').textContent, /Recording the calibration phrases/);
});

test('hidden when the server has no profile endpoint', async () => {
  const { coverage } = make({ '/api/profile': new Error('404') });
  await coverage.load();
  assert.equal(box().hidden, true);
});

test('a feedback event refreshes it, without overlapping loads', async () => {
  let count = 0;
  const { coverage, calls } = make({ '/api/profile': () => { count++; return profile(); },
    '/api/calibration/enrollment-set': { phrases } });
  await Promise.all([coverage.load(), coverage.load()]);
  assert.equal(count, 1);
  document.dispatchEvent(new CustomEvent('heard:feedback'));
  await coverage.loading;
  assert.equal(count, 2);
  assert.ok(calls.includes('/api/calibration/enrollment-set'));
});

test('labels', () => {
  assert.equal(label({ phoneme: 'TH', example: 'thank', vowel: false }), 'the th sound in “thank”');
  assert.equal(label({ phoneme: 'JH', example: 'juice', vowel: false }), 'the j sound in “juice”');
  assert.equal(label({ phoneme: 'AO', example: 'call', vowel: true }), 'the vowel in “call”');
});

test('a malformed reply hides the section instead of throwing', async () => {
  const { coverage } = make({ '/api/profile': { event_id: 'x' } });
  await coverage.load();
  assert.equal(box().hidden, true);
});
