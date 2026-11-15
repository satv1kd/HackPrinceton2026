/* Calibration page controller.

   Flow: intro → mic check → ten sentence cards → finish summary.
   Progress on each card comes from the ProgressTracker's three strategies
   (ASR → voice activity → manual); audio comes from the app's existing
   Recorder; persistence is the local CalibrationStore. Nothing in this flow
   can tell the user they were wrong — the worst that happens is the caret
   waits for a tap. */

import { Recorder } from '../recorder.js';
import { PASSAGE, words as wordsOf } from './passage.js';
import { ProgressTracker } from './tracker.js';
import { createAsr } from './asr.js';
import { MicMonitor } from './vad.js';
import { CalibrationStore } from './storage.js';

const $ = id => document.getElementById(id);
const SCREENS = ['screen-intro', 'screen-mic', 'screen-read', 'screen-finish'];
const AUTO_ADVANCE_MS = 1300;

const store = new CalibrationStore();
const results = new Map();          // sentence id -> saved entry (sans blob ok)
const player = new Audio();

let monitor = null;
let asr = null;
let recorder = null;
let tracker = null;
let cursor = 0;
let singleReturn = false;           // re-recording one sentence from the finish screen
let sentenceSettled = false;        // completion/skip guard for the current card
let autoTimer = null;
let recordingOk = false;

/* ---- screens ----------------------------------------------------------- */

let firstShow = true;

function show(id) {
  for (const s of SCREENS) $(s).hidden = s !== id;
  // Move focus on screen *changes* so screen readers land somewhere sensible,
  // but leave the initial page load alone.
  if (!firstShow) {
    const focusTarget = id === 'screen-read' ? $('sentence') : $(id).querySelector('h1');
    if (focusTarget) {
      focusTarget.setAttribute('tabindex', focusTarget.id === 'sentence' ? '0' : '-1');
      focusTarget.focus({ preventScroll: false });
    }
  }
  firstShow = false;
  window.scrollTo(0, 0);
}

function announce(text) { $('live-region').textContent = text; }

/* ---- mic check --------------------------------------------------------- */

let heardMs = 0;

async function beginMicCheck() {
  show('screen-mic');
  $('mic-denied').hidden = true;
  $('mic-status').textContent = 'Waiting for microphone access…';
  heardMs = 0;
  monitor ??= new MicMonitor({
    onTick: (level, active, dt) => {
      const fill = $('screen-mic').hidden ? $('read-meter-fill') : $('meter-fill');
      fill.style.width = `${Math.round(level * 100)}%`;
      if (!$('screen-mic').hidden && active) {
        heardMs += dt;
        if (heardMs > 600 && $('mic-continue').disabled) {
          $('mic-continue').disabled = false;
          $('mic-status').textContent = 'Sounds good — we can hear you.';
        }
      }
      if (!$('screen-read').hidden && tracker) tracker.voiceTick(active, dt);
    },
  });
  try {
    await monitor.start();
    if ($('mic-continue').disabled) $('mic-status').textContent = 'Go ahead — say anything.';
  } catch (error) {
    $('mic-status').textContent = 'The microphone isn’t available yet.';
    $('mic-denied').hidden = false;
    console.info('microphone request did not succeed:', error?.name);
  }
}

/* ---- reading ----------------------------------------------------------- */

function renderDots() {
  const dots = PASSAGE.map((s, i) => {
    const entry = results.get(s.id);
    const cls = [
      entry && !entry.skipped ? 'done' : entry?.skipped ? 'skipped' : '',
      i === cursor && !$('screen-read').hidden ? 'now' : '',
    ].join(' ').trim();
    return `<i class="${cls}"></i>`;
  });
  $('read-dots').innerHTML = dots.join('');
}

function renderWords() {
  const spans = $('sentence').children;
  for (let i = 0; i < spans.length; i++) {
    spans[i].classList.toggle('spoken', i < tracker.index);
    spans[i].classList.toggle('current', i === tracker.index);
  }
}

function buildSentence(sentence) {
  const container = $('sentence');
  container.setAttribute('aria-label', `Read aloud: ${sentence.text}`);
  container.innerHTML = '';
  wordsOf(sentence).forEach((word, i) => {
    const span = document.createElement('span');
    span.className = 'w';
    span.textContent = word;
    // A tap says "I'm here" — it moves the highlight, it never judges.
    span.addEventListener('click', () => tracker?.moveTo(i));
    container.append(span);
    if (i < wordsOf(sentence).length - 1) container.append(' ');
  });
}

function startSentence(index) {
  clearTimeout(autoTimer);
  cursor = index;
  sentenceSettled = false;
  recordingOk = false;
  const sentence = PASSAGE[cursor];

  show('screen-read');
  $('paused-panel').hidden = true;
  $('read-count').textContent = `Sentence ${cursor + 1} of ${PASSAGE.length}`;
  $('rec-label').textContent = 'Getting the microphone ready…';
  $('rec-dot').hidden = true;
  buildSentence(sentence);
  renderDots();
  announce(`Sentence ${cursor + 1} of ${PASSAGE.length}: ${sentence.text}`);

  tracker = new ProgressTracker(wordsOf(sentence), {
    onProgress: renderWords,
    onComplete: () => completeSentence(),
  });
  renderWords();

  asr ??= createAsr({ onTranscript: text => tracker?.handleTranscript(text) });
  asr.reset();

  recorder = new Recorder({ minDuration: 250, maxDuration: 45000 });
  recorder.start(() => {
    recordingOk = true;
    tracker.start();
    asr.start();
    $('rec-dot').hidden = false;
    $('rec-label').textContent = 'Recording — take your time.';
  }).then(result => {
    if (!result) return; // cancelled by skip / redo / pause
    if (!sentenceSettled) {
      // The 45s safety stop fired before the tracker finished. The take is
      // still good; keep it and move on without a word of reproach.
      sentenceSettled = true;
      asr.stop();
      $('rec-dot').hidden = true;
    }
    saveTake(result);
  }).catch(error => {
    recordingOk = false;
    if (error?.name === 'NotAllowedError') {
      show('screen-mic');
      $('mic-denied').hidden = false;
      $('mic-status').textContent = 'The microphone was blocked.';
      return;
    }
    $('rec-dot').hidden = true;
    if (sentenceSettled) {
      // The reading finished but the take didn't survive (e.g. everything
      // happened inside the recorder's minimum duration). Keep the reading,
      // mark it "no audio", and move on — never strand the card.
      persist({ skipped: false, noAudio: true });
      $('rec-label').textContent = 'That one was too quick to record — moving on.';
      scheduleAdvance();
    } else {
      // Recording failed at the start; reading still works by voice or by
      // hand, and the sentence is marked "no audio" at the end.
      $('rec-label').textContent = 'We couldn’t record just now — your reading still counts.';
    }
    console.info('recorder unavailable:', error?.message);
  });
}

function completeSentence() {
  if (sentenceSettled) return;
  sentenceSettled = true;
  asr.stop();
  $('rec-dot').hidden = true;
  if (recordingOk && recorder.session) {
    $('rec-label').textContent = 'Saving…';
    recorder.stop(); // resolves the start() promise → saveTake()
  } else {
    // No audio to keep; record the reading as done-without-audio.
    persist({ skipped: false, noAudio: true });
    scheduleAdvance();
  }
}

function saveTake({ blob, duration, mimeType }) {
  persist({ skipped: false, blob, mimeType, durationMs: Math.round(duration) });
  $('rec-label').textContent = 'Saved.';
  scheduleAdvance();
}

function persist(extra) {
  const sentence = PASSAGE[cursor];
  const entry = {
    id: sentence.id,
    text: sentence.text,
    focus: sentence.focus,
    recordedAt: new Date().toISOString(),
    timestamps: tracker ? tracker.timestamps : [],
    wordsTracked: tracker ? tracker.index : 0,
    ...extra,
  };
  results.set(sentence.id, entry);
  store.save(entry).catch(error => console.warn('could not persist sentence', error));
  renderDots();
}

function scheduleAdvance() {
  clearTimeout(autoTimer);
  announce('Saved.');
  // Auto-advance is a convenience, never a deadline: Start over cancels it,
  // and the pause button still works during the wait.
  autoTimer = setTimeout(() => advance(), AUTO_ADVANCE_MS);
}

function advance() {
  clearTimeout(autoTimer);
  if (singleReturn) { singleReturn = false; buildFinish(); return; }
  if (cursor + 1 >= PASSAGE.length) { buildFinish(); return; }
  startSentence(cursor + 1);
}

function abandonTake() {
  clearTimeout(autoTimer);
  sentenceSettled = true;  // block a late tracker completion
  asr.stop();
  recorder?.cancel();
}

function skipSentence() {
  abandonTake();
  persist({ skipped: true });
  announce('Skipped.');
  advance();
}

function redoSentence() {
  abandonTake();
  startSentence(cursor);
}

function pauseReading() {
  abandonTake();
  $('rec-dot').hidden = true;
  $('rec-label').textContent = '';
  $('paused-panel').hidden = false;
  $('resume-btn').focus();
  announce('Paused. Nothing is recording.');
}

/* ---- finish ------------------------------------------------------------ */

function buildFinish() {
  show('screen-finish');
  renderDots();
  const recorded = [...results.values()].filter(e => !e.skipped && e.blob).length;
  const skipped = [...results.values()].filter(e => e.skipped).length;
  $('finish-summary').textContent =
    `${recorded} of ${PASSAGE.length} sentences recorded` +
    (skipped ? `, ${skipped} skipped — skipping is allowed.` : '.');
  const list = $('finish-list');
  list.innerHTML = '';
  PASSAGE.forEach((sentence, i) => {
    const entry = results.get(sentence.id);
    const li = document.createElement('li');
    const text = document.createElement('span');
    text.className = 'ftext';
    text.textContent = sentence.text;
    const meta = document.createElement('span');
    meta.className = 'fmeta';
    if (entry?.blob) {
      meta.classList.add('recorded');
      meta.textContent = `Recorded · ${(entry.durationMs / 1000).toFixed(1)}s`;
    } else if (entry?.skipped) meta.textContent = 'Skipped';
    else if (entry?.noAudio) meta.textContent = 'No audio';
    else meta.textContent = '—';
    li.append(text, meta);
    if (entry?.blob) {
      const play = document.createElement('button');
      play.className = 'btn btn-ghost';
      play.textContent = 'Play';
      play.setAttribute('aria-label', `Play your recording of “${sentence.text}”`);
      play.addEventListener('click', () => {
        player.pause();
        if (player.src?.startsWith('blob:')) URL.revokeObjectURL(player.src);
        player.src = URL.createObjectURL(entry.blob);
        player.play().catch(() => {});
      });
      li.append(play);
    }
    const redo = document.createElement('button');
    redo.className = 'btn btn-ghost';
    redo.textContent = entry?.blob ? 'Re-record' : 'Record';
    redo.setAttribute('aria-label', `${redo.textContent} “${sentence.text}”`);
    redo.addEventListener('click', () => { singleReturn = true; startSentence(i); });
    li.append(redo);
    list.append(li);
  });
}

/* ---- wiring ------------------------------------------------------------ */

$('begin-btn').addEventListener('click', beginMicCheck);
$('mic-retry').addEventListener('click', beginMicCheck);
$('mic-continue').addEventListener('click', async () => {
  // Surface anything recorded in an earlier visit so re-records line up.
  try { for (const entry of await store.list()) results.set(entry.id, entry); }
  catch { /* fresh start is fine */ }
  startSentence(0);
});
$('done-btn').addEventListener('click', () => tracker?.moveTo(PASSAGE[cursor] ? wordsOf(PASSAGE[cursor]).length : 0));
$('skip-btn').addEventListener('click', skipSentence);
$('redo-btn').addEventListener('click', redoSentence);
$('pause-btn').addEventListener('click', pauseReading);
$('resume-btn').addEventListener('click', () => startSentence(cursor));

document.addEventListener('keydown', event => {
  if ($('screen-read').hidden || !$('paused-panel').hidden || !tracker) return;
  const onButtons = event.target instanceof Element
    && event.target.closest('button, a, input, select, textarea');
  if (event.key === 'ArrowRight') { event.preventDefault(); tracker.next(); }
  else if (event.key === 'ArrowLeft') { event.preventDefault(); tracker.moveTo(tracker.index - 1); }
  else if (event.key === ' ' && !onButtons) { event.preventDefault(); tracker.next(); }
});

document.addEventListener('visibilitychange', () => {
  const reading = !$('screen-read').hidden && $('paused-panel').hidden;
  if (document.hidden && reading && !sentenceSettled) pauseReading();
});

window.addEventListener('pagehide', () => {
  recorder?.cancel();
  asr?.stop();
  monitor?.stop();
});

show('screen-intro');
