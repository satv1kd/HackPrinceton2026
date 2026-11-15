/* Progress tracking for one sentence of calibration reading.

   Three pluggable strategies feed one tracker, in order of trust:

     1. ASR alignment  — handleTranscript() fuzzy-aligns whatever the browser's
        speech recognition heard (align.js). Best effort; forward only.
     2. Voice activity — voiceTick() advances on speech energy alone when ASR
        has produced nothing useful for ASR_STALE_MS, paced by an estimated
        syllables-per-second rate. For many of our users this IS the primary
        path, because ASR is exactly what fails them.
     3. Manual         — moveTo()/next() from taps and keys. Always wins; the
        only strategy allowed to move backward, because it is the user.

   The tracker never judges: there is no error state, no accuracy, no "try
   again". It only ever answers "how far along are we, and when did each word
   go by" — the timestamps are what make a recording useful later.

   Plain EventTarget-free design (callbacks) so it runs under node:test. */

import { alignTranscript, syllables } from './align.js';

export const ASR_STALE_MS = 1500;
export const DEFAULT_SYLLABLES_PER_SEC = 2.2; // unhurried on purpose

export class ProgressTracker {
  /**
   * @param {string[]} words display words of the sentence
   * @param {{onProgress?: Function, onComplete?: Function,
   *          now?: () => number, pace?: number}} options
   */
  constructor(words, { onProgress, onComplete, now = () => performance.now(), pace = DEFAULT_SYLLABLES_PER_SEC } = {}) {
    this.words = words;
    this.onProgress = onProgress || (() => {});
    this.onComplete = onComplete || (() => {});
    this.now = now;
    this.pace = pace;
    this.index = 0;                 // first word not yet spoken
    this.timestamps = [];           // { word, index, atMs, source }
    this.startedAt = null;
    this.lastProgressAt = null;
    this.speechMs = 0;              // voiced time banked toward the next word
    this.completed = false;
  }

  start() {
    this.startedAt = this.now();
    this.lastProgressAt = this.startedAt;
  }

  get done() { return this.index >= this.words.length; }

  _record(upto, source) {
    const atMs = this.startedAt === null ? null : Math.round(this.now() - this.startedAt);
    for (let i = this.index; i < upto; i++) {
      this.timestamps.push({ word: this.words[i], index: i, atMs, source });
    }
  }

  /** Forward-only advance used by the automatic strategies. */
  advanceTo(target, source) {
    if (this.completed) return;
    const upto = Math.min(this.words.length, target);
    if (upto <= this.index) return;
    this._record(upto, source);
    this.index = upto;
    this.lastProgressAt = this.now();
    this.speechMs = 0;
    this.onProgress(this.index, source);
    if (this.done) {
      this.completed = true;
      this.onComplete();
    }
  }

  /** Manual control: a tap or a key. May move anywhere, including backward. */
  moveTo(target) {
    if (this.completed) return;
    const clamped = Math.max(0, Math.min(this.words.length, target));
    if (clamped > this.index) { this.advanceTo(clamped, 'manual'); return; }
    if (clamped === this.index) return;
    // Backward: the user is redoing part of the sentence. Drop the stale marks.
    this.timestamps = this.timestamps.filter(t => t.index < clamped);
    this.index = clamped;
    this.lastProgressAt = this.now();
    this.speechMs = 0;
    this.onProgress(this.index, 'manual');
  }

  next() { this.moveTo(this.index + 1); }

  /** Strategy 1 — feed the full cumulative ASR transcript (finals + interim). */
  handleTranscript(transcript) {
    const { index, matched } = alignTranscript(this.words, transcript, 0);
    if (matched > 0) this.lastAsrUsefulAt = this.now();
    if (index > this.index) this.advanceTo(index, 'asr');
  }

  /**
   * Strategy 2 — called on every analyser tick (~80ms).
   * @param {boolean} speechActive the mic hears voiced sound right now
   * @param {number} dtMs milliseconds since the last tick
   */
  voiceTick(speechActive, dtMs) {
    if (this.completed || this.startedAt === null) return;
    if (!speechActive) return;
    const sinceProgress = this.now() - this.lastProgressAt;
    if (sinceProgress < ASR_STALE_MS) return; // ASR (or the user) is keeping up
    this.speechMs += dtMs;
    const nextWord = this.words[this.index];
    const needMs = (syllables(nextWord) / this.pace) * 1000;
    if (this.speechMs >= needMs) {
      this.advanceTo(this.index + 1, 'voice');
      // advanceTo reset speechMs; leave lastProgressAt refreshed so pacing
      // continues smoothly word by word while the user keeps talking.
      this.lastProgressAt = this.now() - ASR_STALE_MS; // stay in voice mode
    }
  }
}
