import { Recorder } from './recorder.js';
import { requestJSON, postJSON } from './api.js';
import { TTSPlayer } from './tts.js';
import { CorrectionFlow, labelFor } from './correction.js';
import { renderSendButtons } from './send-buttons.js';

export class TalkMode {
  constructor(app) {
    this.app = app;
    this.recorder = new Recorder();
    this.state = 'idle';
    this.generation = 0;
    this.button = document.getElementById('talk-btn');
    this.toggle = document.getElementById('toggle-record-btn');
    this.status = document.getElementById('talk-status');
    this.result = null;
    this.feedbackBusy = false;
    this.tts = new TTSPlayer();
    this.correction = new CorrectionFlow();
    this.sendContainer = document.getElementById('send-buttons');
    // Which attempt we have already spoken, so a Yes tap on a phrase that
    // was auto-spoken does not say it twice in a row.
    this.autoSpokenAttempt = null;
    this.setupUI();
  }

  setupUI() {
    this.button.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0 || this.state !== 'idle') return;
      event.preventDefault();
      this.input = event.pointerId;
      this.button.setPointerCapture(event.pointerId);
      this.startRecording();
    });
    this.button.addEventListener('pointerup', event => {
      if (this.input !== event.pointerId) return;
      this.input = null;
      this.stopRecording();
    });
    for (const name of ['pointercancel', 'lostpointercapture']) this.button.addEventListener(name, event => {
      if (this.input === event.pointerId) this.cancel();
    });
    this.button.addEventListener('contextmenu', event => event.preventDefault());
    this.button.addEventListener('keydown', event => {
      if (![' ', 'Enter'].includes(event.key)) return;
      event.preventDefault();
      if (!event.repeat && this.state === 'idle') { this.input = event.key; this.startRecording(); }
    });
    this.button.addEventListener('keyup', event => {
      if (event.key !== this.input) return;
      event.preventDefault(); this.input = null; this.stopRecording();
    });
    // The separate tap control supports switch devices and screen-reader activation.
    this.toggle.onclick = () => this.state === 'idle' ? this.startRecording() : this.stopRecording();
    document.getElementById('yes-btn').onclick = () => this.sendFeedback(true, this.result?.candidates[0]);
    document.getElementById('no-btn').onclick = () => this.correct();
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.cancel(); });
    window.addEventListener('pagehide', () => this.cancel());
    window.addEventListener('blur', () => { if (this.state === 'recording') this.cancel(); });
  }

  setState(state, message) {
    this.state = state;
    this.status.textContent = message;
    this.button.classList.toggle('recording', state === 'recording');
    this.button.setAttribute('aria-pressed', String(state === 'recording'));
    this.button.setAttribute('aria-busy', String(state === 'uploading'));
    this.button.disabled = state === 'uploading' || this.feedbackBusy;
    this.toggle.disabled = state === 'uploading' || this.feedbackBusy;
    this.button.querySelector('.label').textContent = { idle: 'Hold to talk', requesting: 'Allow microphone', recording: 'Release to finish', uploading: 'Listening…' }[state];
    this.toggle.textContent = state === 'recording' ? 'Tap to finish' : state === 'requesting' ? 'Cancel recording' : 'Or tap to start / stop';
  }

  async startRecording() {
    if (this.state !== 'idle' || this.feedbackBusy || this.recorder.session) return;
    const generation = ++this.generation;
    this.result = null;
    document.getElementById('result-card').hidden = true;
    // A new utterance retires the previous one: stop any speech, and take the
    // send buttons away so yesterday's phrase can never be sent by mistake.
    this.tts.unlock();
    this.tts.stop();
    this.clearSendButtons();
    this.setState('requesting', 'Allow microphone access if your browser asks.');
    try {
      const recording = await this.recorder.start(() => {
        this.setState('recording', 'Listening. Take your time — up to 10 seconds.');
      });
      if (generation !== this.generation) return;
      this.input = null;
      if (!recording) { this.setState('idle', 'Ready when you are.'); return; }
      this.setState('uploading', 'Finding your words…');
      this.upload = new AbortController();
      const form = new FormData();
      const ext = recording.mimeType.includes('webm') ? 'webm' : recording.mimeType.includes('mp4') ? 'm4a' : recording.mimeType.includes('ogg') ? 'ogg' : 'bin';
      form.append('audio', recording.blob, `recording.${ext}`);
      const result = await requestJSON('/api/recognize', { method: 'POST', body: form, signal: this.upload.signal });
      if (generation !== this.generation) return;
      if (!result.attempt_id || !Array.isArray(result.candidates) || typeof result.confident !== 'boolean') throw new Error('Could not read the recognition result.');
      this.result = result;
      this.showResult();
      this.autoSpeak();
      this.setState('idle', result.candidates.length ? 'Choose a phrase or tell us if we heard you right.' : 'No matching phrases are available yet.');
    } catch (error) {
      if (generation !== this.generation) return;
      const message = error.name === 'NotAllowedError' ? 'Microphone access was denied. Allow it in your browser settings, then try again.' : error.name === 'NotFoundError' ? 'No microphone was found.' : error.message;
      this.setState('idle', message);
    }
  }

  stopRecording() { this.recorder.stop(); }

  cancel() {
    this.generation++;
    this.input = null;
    this.recorder.cancel();
    this.upload?.abort();
    this.tts.stop();
    this.correction.close();
    this.clearSendButtons();
    this.setState('idle', 'Ready when you are.');
  }

  showResult() {
    const { candidates, confident } = this.result;
    // Re-rendering the card (including after an undo) retires any send
    // buttons, so they always belong to the text currently on screen.
    this.clearSendButtons();
    const card = document.getElementById('result-card');
    const list = document.getElementById('candidate-list');
    const feedback = document.getElementById('feedback-buttons');
    list.replaceChildren();
    card.hidden = candidates.length === 0;
    feedback.hidden = !confident || candidates.length === 0;
    if (!candidates.length) return;
    document.getElementById('result-heading').textContent = confident ? 'DID WE HEAR YOU RIGHT?' : 'WHICH ONE DID YOU MEAN?';
    document.getElementById('result-text').textContent = confident ? `${candidates[0].emoji || '💬'} ${candidates[0].text}` : 'Tap the phrase you meant.';
    if (!confident) {
      for (const candidate of candidates.slice(0, 3)) {
        const button = document.createElement('button');
        const generated = candidate.type === 'generated';
        // Generated text comes from the phonetic decoder, which is less
        // reliable than a recorded phrase, so it must look different: the
        // user should be able to tell which guesses are weaker.
        button.textContent = `${candidate.emoji || (generated ? '💬' : '❓')} ${candidate.text}`;
        if (generated) {
          const badge = document.createElement('span');
          badge.className = 'generated-badge';
          badge.textContent = 'Suggested';
          button.append(badge);
        }
        button.onclick = () => this.sendFeedback(true, candidate);
        list.append(button);
      }
      const none = document.createElement('button');
      none.textContent = 'None of these';
      none.onclick = () => this.correct();
      list.append(none);
    }
    card.querySelectorAll('button').forEach(button => { button.disabled = false; });
  }

  /**
   * Speak a confident saved-phrase match straight away.
   *
   * Only saved phrases: generated text from the phonetic decoder is less
   * reliable, so the PRD requires it be confirmed before it is ever spoken
   * or sent. That is why this checks `type === 'phrase'` and not just
   * `confident`.
   */
  autoSpeak() {
    const best = this.result?.candidates?.[0];
    if (!this.result?.confident || !best || best.type !== 'phrase') return;
    this.autoSpokenAttempt = this.result.attempt_id;
    this.tts.speak(best.text);
  }

  /** Open the correction flow and feed its answer back as feedback. */
  async correct() {
    if (!this.result || this.feedbackBusy) return;
    const result = this.result;
    const shown = result.candidates[0];
    const others = result.candidates.filter(candidate => candidate !== shown);

    const label = await this.correction.open({ otherCandidates: others });
    // `undefined` means the user backed out: store nothing at all. `null` is
    // Skip, which is a real negative signal and is stored.
    if (label === undefined) return;
    if (this.result?.attempt_id !== result.attempt_id) return;

    const candidate = label === null
      ? null
      : label.type === 'phrase'
        ? others.find(other => other.phrase_id === label.phrase_id)
          || { type: 'phrase', phrase_id: label.phrase_id, text: label.text || 'Saved phrase' }
        : { type: 'generated', text: label.text };
    await this.sendFeedback(false, candidate, label);
  }

  clearSendButtons() {
    if (this.sendContainer) {
      this.sendContainer.replaceChildren();
      this.sendContainer.hidden = true;
    }
  }

  /** Show send buttons for text the user has confirmed. */
  async showSendButtons(text) {
    if (!this.sendContainer) return;
    this.sendContainer.hidden = false;
    await renderSendButtons({
      container: this.sendContainer,
      text,
      confirmed: true,
      onStatus: (state, detail) => {
        if (state === 'failed') this.status.textContent = detail?.error || 'That message did not send.';
      },
    });
    if (!this.sendContainer.childElementCount) this.sendContainer.hidden = true;
  }

  async sendFeedback(correct, candidate, explicitLabel) {
    if (!this.result || this.feedbackBusy) return;
    const result = this.result;
    const attemptId = result.attempt_id;
    const label = explicitLabel !== undefined ? explicitLabel : labelFor(candidate);
    this.feedbackBusy = true;
    this.setState(this.state, 'Saving your response…');
    document.querySelectorAll('#result-card button').forEach(button => { button.disabled = true; });
    try {
      const started = performance.now();
      const response = await postJSON('/api/feedback', { attempt_id: attemptId, correct, label });
      if (candidate && this.result?.attempt_id === attemptId) {
        document.getElementById('result-heading').textContent = 'YOUR WORDS';
        document.getElementById('result-text').textContent = `${candidate.emoji || '💬'} ${candidate.text}`;
        document.getElementById('candidate-list').replaceChildren();
        document.getElementById('feedback-buttons').hidden = true;
        document.getElementById('result-card').hidden = false;
        // Confirmed, so it is safe to say out loud and safe to send. Skip the
        // speech if this exact attempt was already spoken automatically.
        if (this.autoSpokenAttempt !== attemptId) this.tts.speak(candidate.text);
        this.showSendButtons(candidate.text);
      }
      this.status.textContent = correct ? 'Thank you. Your response is saved.' : 'Thanks for letting us know. You can try again.';
      const duration = Math.min(8000, Date.parse(response.undo_until) - Date.now(), 10000 - (performance.now() - started));
      this.app.showToast('Response saved', duration, async () => {
        await postJSON('/api/feedback/undo', { attempt_id: attemptId, event_id: response.event_id });
        if (this.result?.attempt_id === attemptId) { this.showResult(); this.status.textContent = 'Response undone.'; }
      });
      document.dispatchEvent(new CustomEvent('heard:feedback', { detail: { attemptId, correct, candidate } }));
    } catch (error) {
      this.status.textContent = error.message;
      if (this.result?.attempt_id === attemptId) this.showResult();
    } finally {
      this.feedbackBusy = false;
      this.button.disabled = false;
      this.toggle.disabled = false;
    }
  }
}
