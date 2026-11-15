/* Calibration page controller.

   Flow: intro → mic check → ten sentence cards → finish summary.

   One sentence at a time, recorded whole. An earlier version highlighted each
   word as it was read, from speech recognition with voice activity and taps as
   backstops. It worked, and it was the wrong thing to build: watching a cursor
   chase you turns reading aloud into a typing test, and this page is for
   people for whom reading aloud is already the effort. So the sentence is
   recorded end to end and the server cuts it into words afterwards, from the
   speech-to-text timings — the reader never has to keep up with anything.

   Nothing here can tell the reader they were wrong. The worst outcome is "we
   couldn't line every word up", which is a fact about our cutting, not them. */

import { words as wordsOf } from './passage.js';

const SCREENS = ['screen-intro', 'screen-mic', 'screen-read', 'screen-finish'];
export const AUTO_ADVANCE_MS = 1400;
/* A sentence is seven words or fewer; 45s is a generous ceiling for anyone's
   pace, and it only exists so a forgotten tab stops recording. */
export const MAX_SENTENCE_MS = 45000;

export class CalibrationFlow {
  constructor({ doc, passage, api, makeRecorder, makeMonitor, autoAdvanceMs = AUTO_ADVANCE_MS } = {}) {
    this.doc = doc ?? globalThis.document;
    this.passage = passage;
    this.api = api;
    this.makeRecorder = makeRecorder;
    this.makeMonitor = makeMonitor;
    this.autoAdvanceMs = autoAdvanceMs;

    this.saved = new Map();     // sentence id -> the server's record
    this.cursor = 0;
    this.state = 'ready';       // ready | recording | saving | saved
    this.recorder = null;
    this.monitor = null;
    this.autoTimer = null;
    this.singleReturn = false;  // re-recording one card from the finish screen
    this.firstShow = true;
    this.heardMs = 0;
    this.player = null;
  }

  $(id) { return this.doc.getElementById(id); }

  /* ---- screens --------------------------------------------------------- */

  show(id) {
    for (const screen of SCREENS) {
      const element = this.$(screen);
      if (element) element.hidden = screen !== id;
    }
    if (!this.firstShow) {
      const target = id === 'screen-read' ? this.$('sentence') : this.$(id)?.querySelector('h1');
      if (target) {
        target.setAttribute('tabindex', target.id === 'sentence' ? '0' : '-1');
        target.focus?.({ preventScroll: false });
      }
    }
    this.firstShow = false;
    globalThis.scrollTo?.(0, 0);
  }

  announce(text) {
    const region = this.$('live-region');
    if (region) region.textContent = text;
  }

  status(text) {
    const label = this.$('rec-label');
    if (label) label.textContent = text;
  }

  /* ---- mic check ------------------------------------------------------- */

  async beginMicCheck() {
    this.show('screen-mic');
    this.$('mic-denied').hidden = true;
    this.$('mic-continue').disabled = true;
    this.$('mic-status').textContent = 'Waiting for microphone access…';
    this.heardMs = 0;
    this.monitor ??= this.makeMonitor({ onTick: (level, active, dt) => this.onLevel(level, active, dt) });
    try {
      await this.monitor.start();
      if (this.$('mic-continue').disabled) this.$('mic-status').textContent = 'Go ahead — say anything.';
    } catch (error) {
      this.$('mic-status').textContent = 'The microphone isn’t available yet.';
      this.$('mic-denied').hidden = false;
      console.info('microphone request did not succeed:', error?.name);
    }
  }

  onLevel(level, active, dt) {
    const onMic = !this.$('screen-mic').hidden;
    const fill = onMic ? this.$('meter-fill') : this.$('read-meter-fill');
    if (fill) fill.style.width = `${Math.round(level * 100)}%`;
    if (!onMic || !active) return;
    this.heardMs += dt;
    if (this.heardMs > 600 && this.$('mic-continue').disabled) {
      this.$('mic-continue').disabled = false;
      this.$('mic-status').textContent = 'Sounds good — we can hear you.';
    }
  }

  async startReading() {
    // Anything recorded in an earlier visit, so re-records line up.
    try { for (const record of await this.api.list()) this.saved.set(record.id, record); }
    catch { /* a fresh start is fine */ }
    this.startSentence(0);
  }

  /* ---- one card -------------------------------------------------------- */

  startSentence(index) {
    clearTimeout(this.autoTimer);
    this.cursor = index;
    this.state = 'ready';
    const sentence = this.passage[index];
    this.show('screen-read');
    this.$('paused-panel').hidden = true;
    this.$('read-count').textContent = `Sentence ${index + 1} of ${this.passage.length}`;
    this.$('sentence').textContent = sentence.text;
    this.$('rec-dot').hidden = true;
    this.renderDots();
    this.renderControls();
    const already = this.saved.get(sentence.id);
    this.status(already ? 'Recorded already — read it again whenever you like.' : '');
    this.announce(`Sentence ${index + 1} of ${this.passage.length}: ${sentence.text}`);
  }

  renderControls() {
    const recording = this.state === 'recording';
    const record = this.$('record-btn');
    const done = this.$('done-btn');
    if (record) {
      record.hidden = recording;
      record.disabled = this.state === 'saving';
      record.textContent = this.saved.has(this.passage[this.cursor].id) && this.state !== 'saved'
        ? 'Record it again' : 'Start recording';
    }
    if (done) done.hidden = !recording;
    const redo = this.$('redo-btn');
    if (redo) redo.hidden = !recording;
    const skip = this.$('skip-btn');
    if (skip) skip.disabled = this.state === 'saving';
  }

  renderDots() {
    const dots = this.passage.map((sentence, index) => {
      const record = this.saved.get(sentence.id);
      const classes = [record ? 'done' : '', index === this.cursor ? 'now' : ''].join(' ').trim();
      return `<i class="${classes}"></i>`;
    });
    const container = this.$('read-dots');
    if (container) container.innerHTML = dots.join('');
  }

  async startRecording() {
    if (this.state === 'recording' || this.state === 'saving') return;
    this.state = 'recording';
    this.renderControls();
    this.status('Getting the microphone ready…');
    this.recorder = this.makeRecorder();
    let take;
    try {
      take = await this.recorder.start(() => {
        this.$('rec-dot').hidden = false;
        this.status('Recording — read it at your own pace, then press “I’ve read it”.');
      });
    } catch (error) {
      this.state = 'ready';
      this.$('rec-dot').hidden = true;
      this.renderControls();
      if (error?.name === 'NotAllowedError') {
        this.show('screen-mic');
        this.$('mic-denied').hidden = false;
        this.$('mic-status').textContent = 'The microphone was blocked.';
        return;
      }
      this.status(error?.message || 'We couldn’t record just now.');
      return;
    }
    this.$('rec-dot').hidden = true;
    if (!take) {                       // cancelled by Start over, Skip or Pause
      if (this.state === 'recording') { this.state = 'ready'; this.renderControls(); }
      return;
    }
    await this.upload(take);
  }

  /* "I've read it" — the reader decides when the sentence is finished. */
  finishReading() {
    if (this.state !== 'recording') return;
    this.status('Saving…');
    this.recorder?.stop();
  }

  async upload({ blob, mimeType }) {
    const sentence = this.passage[this.cursor];
    this.state = 'saving';
    this.renderControls();
    this.status('Saving…');
    try {
      const record = await this.api.save({ id: sentence.id, text: sentence.text, blob, mimeType });
      this.saved.set(sentence.id, record);
      this.state = 'saved';
      this.status(savedMessage(record));
      this.announce('Saved.');
      this.renderDots();
      this.renderControls();
      this.scheduleAdvance();
    } catch (error) {
      this.state = 'ready';
      this.renderControls();
      // Keep the card where it is: a failed save is ours to fix, and the
      // reader should never be pushed on past a sentence that didn't land.
      this.status(error?.message || 'That one didn’t save. Try it again.');
    }
  }

  scheduleAdvance() {
    clearTimeout(this.autoTimer);
    this.autoTimer = setTimeout(() => this.advance(), this.autoAdvanceMs);
  }

  advance() {
    clearTimeout(this.autoTimer);
    if (this.singleReturn) { this.singleReturn = false; this.buildFinish(); return; }
    if (this.cursor + 1 >= this.passage.length) { this.buildFinish(); return; }
    this.startSentence(this.cursor + 1);
  }

  abandonTake() {
    clearTimeout(this.autoTimer);
    this.recorder?.cancel();
    this.state = 'ready';
    this.$('rec-dot').hidden = true;
  }

  skipSentence() {
    this.abandonTake();
    this.announce('Skipped.');
    this.advance();
  }

  redoSentence() {
    this.abandonTake();
    this.startSentence(this.cursor);
  }

  pauseReading() {
    this.abandonTake();
    this.status('');
    this.$('paused-panel').hidden = false;
    this.$('resume-btn')?.focus?.();
    this.announce('Paused. Nothing is recording.');
  }

  /* ---- finish ---------------------------------------------------------- */

  buildFinish() {
    this.cursor = -1;
    this.show('screen-finish');
    this.renderDots();
    const recorded = this.saved.size;
    const placed = [...this.saved.values()].reduce((sum, r) => sum + (r.placed_count || 0), 0);
    const words = this.passage.reduce((sum, s) => sum + wordsOf(s).length, 0);
    this.$('finish-summary').textContent =
      `${recorded} of ${this.passage.length} sentences recorded` +
      (recorded ? `, ${placed} of ${words} words cut into their own clips.` : '.');
    const list = this.$('finish-list');
    list.innerHTML = '';
    this.passage.forEach((sentence, index) => {
      list.append(this.finishRow(sentence, index));
    });
  }

  finishRow(sentence, index) {
    const record = this.saved.get(sentence.id);
    const li = this.doc.createElement('li');
    const text = this.doc.createElement('span');
    text.className = 'ftext';
    text.textContent = sentence.text;
    const meta = this.doc.createElement('span');
    meta.className = 'fmeta';
    if (record) {
      meta.classList.add('recorded');
      meta.textContent = `${record.placed_count} of ${record.word_count} words · ${(record.duration_sec || 0).toFixed(1)}s`;
    } else {
      meta.textContent = 'Not recorded';
    }
    li.append(text, meta);
    if (record) li.append(this.playButton(sentence, record));
    const redo = this.doc.createElement('button');
    redo.className = 'btn btn-ghost';
    redo.textContent = record ? 'Re-record' : 'Record';
    redo.setAttribute('aria-label', `${redo.textContent} “${sentence.text}”`);
    redo.addEventListener('click', () => { this.singleReturn = true; this.startSentence(index); });
    li.append(redo);
    return li;
  }

  playButton(sentence) {
    const play = this.doc.createElement('button');
    play.className = 'btn btn-ghost';
    play.textContent = 'Play';
    play.setAttribute('aria-label', `Play your recording of “${sentence.text}”`);
    play.addEventListener('click', () => {
      if (typeof Audio !== 'function') return;
      this.player ??= new Audio();
      this.player.pause();
      this.player.src = this.api.audioUrl(sentence.id);
      this.player.play?.().catch(() => {});
    });
    return play;
  }

  /* ---- wiring ---------------------------------------------------------- */

  attach() {
    const on = (id, handler) => this.$(id)?.addEventListener('click', handler);
    on('begin-btn', () => this.beginMicCheck());
    on('mic-retry', () => this.beginMicCheck());
    on('mic-continue', () => this.startReading());
    on('record-btn', () => this.startRecording());
    on('done-btn', () => this.finishReading());
    on('redo-btn', () => this.redoSentence());
    on('skip-btn', () => this.skipSentence());
    on('pause-btn', () => this.pauseReading());
    on('resume-btn', () => this.startSentence(this.cursor));

    this.doc.addEventListener('keydown', event => {
      if (this.$('screen-read').hidden || !this.$('paused-panel').hidden) return;
      const onControl = event.target instanceof Element
        && event.target.closest('button, a, input, select, textarea');
      if (event.key !== ' ' || onControl) return;
      // Space is the one shortcut: start, then finish. Never a word cursor.
      event.preventDefault();
      if (this.state === 'ready') this.startRecording();
      else if (this.state === 'recording') this.finishReading();
    });

    this.doc.addEventListener('visibilitychange', () => {
      const reading = !this.$('screen-read').hidden && this.$('paused-panel').hidden;
      if (this.doc.hidden && reading && this.state === 'recording') this.pauseReading();
    });

    globalThis.addEventListener?.('pagehide', () => {
      this.recorder?.cancel();
      this.monitor?.stop();
    });
    this.show('screen-intro');
    return this;
  }
}

export function savedMessage(record) {
  const placed = record?.placed_count ?? 0;
  const total = record?.word_count ?? 0;
  if (!total) return 'Saved.';
  if (placed === total) return `Saved — all ${total} words lined up.`;
  if (placed === 0) {
    // The take is kept either way; say so, and don't imply they misread it.
    return 'Saved. We couldn’t line the words up this time — the recording is still kept.';
  }
  return `Saved — ${placed} of ${total} words lined up.`;
}
