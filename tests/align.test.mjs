// Fuzzy alignment for calibration reading (static/js/calibration/align.js).
//
// The transcripts fed in here are deliberately mangled, because that is what
// speech recognition produces for the people Heard is built for. The module's
// contract: generous matching, forward-only movement, no concept of "wrong".
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  normalizeWord, tokenize, levenshtein, phoneticKey, wordsMatch,
  alignTranscript, syllables,
} from '../static/js/calibration/align.js';

const SENTENCE = 'The sun rose over the quiet harbor.'.split(' ');

test('normalize and tokenize strip punctuation, case, and smart quotes', () => {
  assert.equal(normalizeWord('“Don’t,”'), 'dont');
  assert.deepEqual(tokenize('  The sun—  rose!! '), ['the', 'sun', 'rose']);
  assert.deepEqual(tokenize('the sun - rose'), ['the', 'sun', 'rose']);
});

test('levenshtein basics', () => {
  assert.equal(levenshtein('harbor', 'harbor'), 0);
  assert.equal(levenshtein('harbor', 'harbour'), 1);
  assert.equal(levenshtein('', 'abc'), 3);
});

test('phonetic key treats sound-alike spellings as equal', () => {
  assert.equal(phoneticKey('rose'), phoneticKey('rows'));
  assert.equal(phoneticKey('quiet'), phoneticKey('quite'));
  assert.equal(phoneticKey('sun'), phoneticKey('son'));
  assert.equal(phoneticKey('phone'), phoneticKey('fone'));
  assert.notEqual(phoneticKey('harbor'), phoneticKey('morning'));
});

test('wordsMatch is generous but not indiscriminate', () => {
  assert.ok(wordsMatch('harbor', 'harbour'));
  assert.ok(wordsMatch('children', 'childrens'));
  assert.ok(wordsMatch('rose', 'rows'));
  assert.ok(wordsMatch('the', 'the'));
  assert.ok(!wordsMatch('sun', 'window'));
  assert.ok(!wordsMatch('the', 'strong'));
  assert.ok(!wordsMatch('', 'sun'));
});

test('a clean transcript completes the sentence', () => {
  const { index, matched } = alignTranscript(SENTENCE, 'the sun rose over the quiet harbor');
  assert.equal(index, SENTENCE.length);
  assert.equal(matched, 7);
});

test('a mangled transcript still completes the sentence', () => {
  // What a recognizer actually returned for dysarthric speech in our tests:
  // homophones, dropped words, mergers.
  const { index } = alignTranscript(SENTENCE, 'uh the son rows over quite harbour');
  assert.equal(index, SENTENCE.length);
});

test('dropped little words are jumped over, not held against the reader', () => {
  const { index } = alignTranscript(SENTENCE, 'sun rose quiet harbor');
  assert.equal(index, SENTENCE.length);
});

test('growing interim transcripts only ever move forward', () => {
  let at = 0;
  const interims = ['the', 'the sun', 'the sun rose', 'the sun ro', 'the sun rose over the'];
  const seen = [];
  for (const t of interims) {
    at = Math.max(at, alignTranscript(SENTENCE, t).index);
    seen.push(at);
  }
  for (let i = 1; i < seen.length; i++) assert.ok(seen[i] >= seen[i - 1], 'index regressed');
  assert.equal(at, 5);
});

test('fromIndex is a floor: stale early words cannot pull the caret back', () => {
  const { index } = alignTranscript(SENTENCE, 'sun rose', 4);
  assert.equal(index, 4);
});

test('repetitions and fillers do not race ahead', () => {
  const { index } = alignTranscript(SENTENCE, 'the the um the sun sun');
  assert.equal(index, 2); // "the sun" spoken; nothing beyond
});

test('an empty or useless transcript reports zero matches', () => {
  assert.deepEqual(alignTranscript(SENTENCE, ''), { index: 0, matched: 0 });
  const noise = alignTranscript(SENTENCE, 'banana spaceship'); // nothing resembles these
  assert.equal(noise.index, 0);
  assert.equal(noise.matched, 0);
});

test('lookahead is bounded: a late word cannot skip most of the sentence', () => {
  const { index } = alignTranscript(SENTENCE, 'harbor');
  assert.equal(index, 0); // "harbor" is 6 words ahead; out of lookahead range
});

test('syllable estimates are sane enough to pace a fallback', () => {
  assert.equal(syllables('sun'), 1);
  assert.equal(syllables('over'), 2);
  assert.equal(syllables('harbor'), 2);
  assert.equal(syllables('quiet'), 1); // diphthong-blind by design: an estimate for pacing, not phonology
  assert.equal(syllables('rose'), 1);   // silent e
  assert.equal(syllables('little'), 2); // -le keeps its syllable
});
