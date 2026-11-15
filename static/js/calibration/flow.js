/* Calibration page controller.

   Flow: intro → mic check → one card per phrase per take → finish summary.
   The phrases are the built-in enrollment set (short, everyday, covering every
   sound of English), each recorded `takes` times.

   One sentence at a time, recorded whole. An earlier version highlighted each
   word as it was read, from speech recognition with voice activity and taps as
   backstops. It worked, and it was the wrong thing to build: watching a cursor
   chase you turns reading aloud into a typing test, and this page is for
   people for whom reading aloud is already the effort. So the sentence is
   recorded end to end and the server cuts it into words afterwards, from the
   speech-to-text timings — the reader never has to keep up with anything.

   Nothing here can tell the reader they were wrong. The worst outcome is "we
   couldn't line every word up", which is a fact about our cutting, not them. */


/* A phrase is recorded `takes` times. Each take is its own card; take 1 keeps
   the bare phrase id as its key (as the server does), later takes add "#n". */
export const takeKey = (id, take = 1) => (take === 1 ? id : `${id}#${take}`);

const SCREENS = ['screen-intro', 'screen-mic', 'screen-read', 'screen-finish'];
export const AUTO_ADVANCE_MS = 1400;
/* A sentence is seven words or fewer; 45s is a generous ceiling for anyone's
   pace, and it only exists so a forgotten tab stops recording. */
export const MAX_SENTENCE_MS = 45000;

export class CalibrationFlow {
  constructor({ doc, passage, api, makeRecorder, makeMonitor, autoAdvanceMs = AUTO_ADVANCE_MS, takes = 1 } = {}) {
    this.doc = doc ?? globalThis.document;
    this.passage = passage;
    this.takes = Math.max(1, takes);
    // One card per take: [{ ...phrase, take, phraseIndex, key }].
    this.cards = passage.flatMap((phrase, phraseIndex) =>
      Array.from({ length: this.takes }, (_, i) => ({
        ...phrase, take: i + 1, phraseIndex, key: takeKey(phrase.id, i + 1),
      })));
    this.api = api;
    this.makeRecorder = makeRecorder;
    this.makeMonitor = makeMonitor;
    this.autoAdvanceMs = autoAdvanceMs;

    this.saved = new Map();     // takeKey(id, take) -> the server's record
    this.cursor = 0;
    this.state = 'ready';       // ready | recording | saving | saved
    this.recorder = null;
    this.monitor = null;
    this.autoTimer = null;
    this.singleReturn = null;   // phrase id being re-recorded from the finish screen
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
        // Focusable for screen readers, but not a Tab stop: nothing to operate.
        target.setAttribute('tabindex', '-1');
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
    try { for (const record of await this.api.list()) this.saved.set(takeKey(record.id, record.take || 1), record); }
    catch { /* a fresh start is fine */ }
    // Resume at the first take not recorded yet (or the start, if all are).
    const next = this.cards.findIndex(card => !this.saved.has(card.key));
    this.startSentence(next === -1 ? 0 : next);
  }

  /* ---- one card -------------------------------------------------------- */

  startSentence(index) {
    clearTimeout(this.autoTimer);
    this.cursor = index;
    this.state = 'ready';
    const card = this.cards[index];
    this.show('screen-read');
    this.$('paused-panel').hidden = true;
    this.$('read-count').textContent = this.countLabel(card);
    this.$('sentence').textContent = card.text;
    this.$('rec-dot').hidden = true;
    this.renderDots();
    this.renderControls();
    const already = this.saved.get(card.key);
    this.status(already ? 'Recorded already — say it again whenever you like.'
      : card.take > 1 ? 'Once more, the same phrase — however it comes out is right.' : '');
    this.announce(`${this.countLabel(card)}: ${card.text}`);
  }

  countLabel(card) {
    if (this.takes === 1) return `Sentence ${card.phraseIndex + 1} of ${this.passage.length}`;
    return `Phrase ${card.phraseIndex + 1} of ${this.passage.length} · take ${card.take} of ${this.takes}`;
  }

  renderControls() {
    const recording = this.state === 'recording';
    const record = this.$('record-btn');
    const done = this.$('done-btn');
    if (record) {
      record.hidden = recording;
      record.disabled = this.state === 'saving';
      record.textContent = this.saved.has(this.cards[this.cursor].key) && this.state !== 'saved'
        ? 'Record it again' : 'Start recording';
    }
    if (done) done.hidden = !recording;
    const redo = this.$('redo-btn');
    if (redo) redo.hidden = !recording;
    const skip = this.$('skip-btn');
    if (skip) skip.disabled = this.state === 'saving';
  }

  takesDone(phrase) {
    let done = 0;
    for (let take = 1; take <= this.takes; take++) if (this.saved.has(takeKey(phrase.id, take))) done++;
    return done;
  }

  renderDots() {
    const current = this.cards[this.cursor]?.phraseIndex;
    const dots = this.passage.map((phrase, index) => {
      const complete = this.takesDone(phrase) === this.takes;
      const classes = [complete ? 'done' : '', index === current ? 'now' : ''].join(' ').trim();
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
        this.status('Recording — say it your own way, then press “I’ve said it”.');
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
    const card = this.cards[this.cursor];
    this.state = 'saving';
    this.renderControls();
    this.status('Saving…');
    try {
      const record = await this.api.save({ id: card.id, take: card.take, text: card.text, blob, mimeType });
      this.saved.set(card.key, record);
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
    const next = this.cards[this.cursor + 1];
    if (this.singleReturn) {
      // Re-recording one phrase: do its remaining takes, then back to the list.
      if (next && next.id === this.singleReturn) { this.startSentence(this.cursor + 1); return; }
      this.singleReturn = null;
      this.buildFinish();
      return;
    }
    if (!next) { this.buildFinish(); return; }
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
    const recorded = this.passage.filter(p => this.takesDone(p) > 0).length;
    const noun = this.takes === 1 ? 'sentences' : 'phrases';
    const complete = this.passage.filter(p => this.takesDone(p) === this.takes).length;
    this.$('finish-summary').textContent = this.takes === 1
      ? `${recorded} of ${this.passage.length} ${noun} recorded.`
      : `${complete} of ${this.passage.length} ${noun} recorded twice.`;
    const list = this.$('finish-list');
    list.innerHTML = '';
    this.passage.forEach((sentence, index) => {
      list.append(this.finishRow(sentence, index));
    });
  }

  finishRow(sentence, index) {
    const takes = Array.from({ length: this.takes }, (_, i) => this.saved.get(takeKey(sentence.id, i + 1)));
    const record = takes.find(Boolean);
    const li = this.doc.createElement('li');
    const text = this.doc.createElement('span');
    text.className = 'ftext';
    text.textContent = sentence.text;
    const meta = this.doc.createElement('span');
    meta.className = 'fmeta';
    if (record && this.takes === 1) {
      meta.classList.add('recorded');
      meta.textContent = `Recorded · ${(record.duration_sec || 0).toFixed(1)}s`;
    } else if (record) {
      const done = takes.filter(Boolean).length;
      meta.classList.add('recorded');
      meta.textContent = `${done} of ${this.takes} takes`;
    } else {
      meta.textContent = 'Not recorded';
    }
    li.append(text, meta);
    if (record) li.append(this.playButton(sentence, record));
    const redo = this.doc.createElement('button');
    redo.className = 'btn btn-ghost';
    redo.textContent = record ? 'Re-record' : 'Record';
    redo.setAttribute('aria-label', `${redo.textContent} “${sentence.text}”`);
    redo.addEventListener('click', () => {
      this.singleReturn = sentence.id;
      this.startSentence(this.cards.findIndex(card => card.id === sentence.id && card.take === 1));
    });
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
      // The latest take that exists.
      let take = this.takes;
      while (take > 1 && !this.saved.has(takeKey(sentence.id, take))) take--;
      this.player.src = this.api.audioUrl(sentence.id, take);
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

/* Nothing about the take is judged, so there is nothing to report but that
   it is kept; the sound engine learns from it in the background. */
export function savedMessage() {
  return 'Saved.';
}
