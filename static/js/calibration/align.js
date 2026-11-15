/* Fuzzy forward alignment of a speech-to-text transcript against the words the
   user was asked to read.

   The whole point of Heard is that off-the-shelf recognition gets our users
   wrong, so this module is deliberately generous: a word counts as spoken if
   the transcript has anything that *resembles* it — by edit distance or by a
   rough phonetic key — and alignment only ever moves forward. There is no
   notion of a mistake anywhere in here.

   Pure functions only (no DOM, no timers), so it can be unit-tested with
   messy transcripts. See tests/align.test.mjs. */

/** Lowercase a word and strip everything but letters, digits, and interior
 *  apostrophes. "Don’t," -> "dont". */
export function normalizeWord(word) {
  return String(word)
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')     // diacritics
    .replace(/[’']/g, '')
    .replace(/[^a-z0-9]/g, '');
}

/** Split free text into normalized word tokens. */
export function tokenize(text) {
  return String(text).split(/\s+/).map(normalizeWord).filter(Boolean);
}

/** Classic Levenshtein edit distance, two-row implementation. */
export function levenshtein(a, b) {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(
        prev[j] + 1,
        row[j - 1] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
    prev = row;
  }
  return prev[b.length];
}

/* A compact sound-class key, in the spirit of Metaphone but much smaller:
   spelling quirks are flattened first, then consonants collapse into broad
   articulation classes and vowels drop out. "rose" and "rows" share a key;
   so do "quiet" and "quite", "sun" and "son". */
const SPELLING = [
  [/ph/g, 'f'], [/ght/g, 't'], [/gh/g, ''], [/^kn/, 'n'], [/^wr/, 'r'],
  [/^wh/, 'w'], [/wh/g, 'w'], [/ck/g, 'k'], [/sch/g, 'sk'], [/^x/, 's'],
  [/x/g, 'ks'], [/q/g, 'k'], [/dg/g, 'j'], [/tch/g, 'c'], [/ch/g, 'c'],
  [/sh/g, 'x'], [/th/g, '0'], [/c(?=[eiy])/g, 's'], [/c/g, 'k'],
];
const CLASS = {
  b: 'p', p: 'p', f: 'f', v: 'f', t: 't', d: 't', s: 's', z: 's',
  k: 'k', g: 'k', j: 'j', m: 'n', n: 'n', l: 'l', r: 'r',
  c: 'c', x: 'x', 0: '0',
};

export function phoneticKey(word) {
  let w = normalizeWord(word);
  if (!w) return '';
  for (const [pattern, replacement] of SPELLING) w = w.replace(pattern, replacement);
  let key = /^[aeiouy]/.test(w) ? 'a' : '';
  for (const ch of w) {
    const cls = CLASS[ch];
    if (cls && key[key.length - 1] !== cls) key += cls;
  }
  return key || 'a';
}

/** Loose equivalence between an expected word and a heard word. */
export function wordsMatch(expected, heard) {
  const e = normalizeWord(expected);
  const h = normalizeWord(heard);
  if (!e || !h) return false;
  if (e === h) return true;
  // Tiny words carry little signal; insist on a phonetic agreement for them.
  if (e.length <= 2 || h.length <= 2) return phoneticKey(e) === phoneticKey(h);
  const budget = Math.max(1, Math.floor(Math.max(e.length, h.length) / 3));
  if (levenshtein(e, h) <= budget) return true;
  return phoneticKey(e) === phoneticKey(h);
}

/**
 * Greedy, forward-only alignment.
 *
 * Walks the transcript once; each heard token may claim the next expected
 * word or jump a small LOOKAHEAD forward (the recognizer often drops little
 * words entirely). Jumped-over words are treated as spoken — we trust the
 * reader, not the recognizer.
 *
 * @param {string[]} expectedWords words of the sentence being read
 * @param {string}   transcript    cumulative ASR text (finals + interim)
 * @param {number}   fromIndex     first word not yet marked spoken
 * @returns {{ index: number, matched: number }} new index (never lower than
 *          fromIndex) and how many transcript tokens found a home — the
 *          caller's measure of whether ASR is being useful at all.
 */
export function alignTranscript(expectedWords, transcript, fromIndex = 0, lookahead = 3) {
  const expected = expectedWords.map(normalizeWord);
  let index = Math.max(0, fromIndex);
  let matched = 0;
  for (const token of tokenize(transcript)) {
    // A repeat of the word just completed is a restart or a disfluency
    // ("the— the sun"), not new ground. Ignore it, so it can never jump the
    // lookahead window to a later copy of the same word.
    if (index > 0 && wordsMatch(expected[index - 1], token)) continue;
    const limit = Math.min(index + lookahead, expected.length - 1);
    for (let j = index; j <= limit; j++) {
      if (wordsMatch(expected[j], token)) {
        index = j + 1;
        matched++;
        break;
      }
    }
    if (index >= expected.length) break;
  }
  return { index, matched };
}

/** Rough syllable count, for the voice-activity pacing fallback. */
export function syllables(word) {
  const w = normalizeWord(word);
  if (!w) return 0;
  const groups = w.match(/[aeiouy]+/g);
  let count = groups ? groups.length : 1;
  if (count > 1 && /[^aeiouy]e$/.test(w) && !/le$/.test(w)) count--;
  return Math.max(1, count);
}
