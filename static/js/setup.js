/**
 * Setup mode: phrase management and sample recording (Task 9).
 *
 * Replaces the read-only phrase list that app.js shipped with. Covers
 * creating a phrase (text plus emoji or photo, personal-word toggle),
 * recording three samples each with playback and re-record, and deleting
 * phrases and individual samples.
 *
 * Phrase text is user input and is always set with `textContent`, never
 * interpolated into `innerHTML`: a phrase containing `<` must not be able to
 * rewrite the page.
 *
 * "Personal word" marks something like "wawa" for water. The server keeps it
 * out of phonetic learning (it does not follow standard pronunciation) while
 * still matching it as a whole phrase, so the badge is worth showing.
 */

import { requestJSON } from './api.js';
import { Recorder } from './recorder.js';

const SAMPLES_WANTED = 3;
const SUGGESTED_EMOJI = ['💧', '🍽️', '☕', '🚻', '💊', '🚌', '👋', '🙏', '❤️', '😀', '🆘', '💬'];

export class SetupMode {
  constructor(app, { container, statusEl, editorEl } = {}) {
    this.app = app;
    this.list = container || document.getElementById('phrase-list');
    this.status = statusEl || document.getElementById('setup-status');
    this.editor = editorEl || document.getElementById('setup-editor');
    this.phrases = [];
    this.flow = null;
  }

  say(message) {
    if (this.status) this.status.textContent = message;
  }

  /* ------------------------------------------------------------- loading */

  async load() {
    this.say('Loading your phrases…');
    try {
      const phrases = await requestJSON('/api/phrases');
      if (!Array.isArray(phrases)) throw new Error('Could not read your phrases.');
      this.phrases = phrases;
      this.render();
      this.say(phrases.length ? '' : 'No phrases yet. Add your first one below.');
    } catch (error) {
      this.say(error.message);
    }
  }

  render() {
    this.renderEditor();
    this.list.replaceChildren(...this.phrases.map(phrase => this.card(phrase)));
  }

  /* --------------------------------------------------------------- cards */

  card(phrase) {
    const card = document.createElement('article');
    card.className = 'phrase-card';
    card.dataset.id = phrase.id;

    const icon = document.createElement('div');
    icon.className = 'phrase-icon';
    if (phrase.image_url) {
      const img = document.createElement('img');
      img.src = phrase.image_url;
      img.alt = '';
      icon.append(img);
    } else {
      icon.textContent = phrase.emoji || '💬';
    }

    const body = document.createElement('div');
    body.className = 'phrase-body';

    const text = document.createElement('p');
    text.className = 'phrase-text';
    text.textContent = phrase.text;

    const count = document.createElement('p');
    const recorded = phrase.sample_count ?? phrase.samples?.length ?? 0;
    count.className = recorded >= SAMPLES_WANTED ? 'sample-count done' : 'sample-count';
    count.textContent = `${recorded} of ${SAMPLES_WANTED} recordings`;
    body.append(text, count);

    if (phrase.personal_word) {
      const badge = document.createElement('span');
      badge.className = 'badge';
      badge.textContent = 'My own word';
      body.append(badge);
    }

    const actions = document.createElement('div');
    actions.className = 'phrase-actions';

    const record = document.createElement('button');
    record.type = 'button';
    record.className = 'icon-btn';
    record.textContent = '🎤';
    record.setAttribute('aria-label', `Record ${phrase.text}`);
    record.onclick = () => this.startRecording(phrase);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-btn danger';
    remove.textContent = '🗑️';
    remove.setAttribute('aria-label', `Delete ${phrase.text}`);
    remove.onclick = () => this.deletePhrase(phrase);

    actions.append(record, remove);

    if (recorded > 0) {
      const manage = document.createElement('button');
      manage.type = 'button';
      manage.className = 'text-button';
      manage.textContent = 'Recordings';
      manage.onclick = () => this.toggleSamples(card, phrase);
      actions.append(manage);
    }

    card.append(icon, body, actions);
    return card;
  }

  /** Expand a card to list its individual samples, so one can be removed. */
  async toggleSamples(card, phrase) {
    const existing = card.querySelector('.sample-list');
    if (existing) {
      existing.remove();
      return;
    }
    const list = document.createElement('ul');
    list.className = 'sample-list';
    card.append(list);

    let samples = phrase.samples;
    if (!Array.isArray(samples)) {
      try {
        samples = (await requestJSON(`/api/phrases/${phrase.id}`)).samples || [];
      } catch (error) {
        this.say(error.message);
        return;
      }
    }

    list.replaceChildren(
      ...samples.map((sample, index) => {
        const item = document.createElement('li');

        const play = document.createElement('button');
        play.type = 'button';
        play.className = 'text-button';
        play.textContent = `▶︎ Recording ${index + 1}`;
        play.onclick = () => {
          new Audio(`/api/phrases/${phrase.id}/samples/${sample.id}/audio`).play().catch(() => {
            this.say('Could not play that recording.');
          });
        };

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'icon-btn danger';
        remove.textContent = '🗑️';
        remove.setAttribute('aria-label', `Delete recording ${index + 1}`);
        remove.onclick = () => this.deleteSample(phrase, sample);

        item.append(play, remove);
        return item;
      })
    );
    if (!samples.length) list.append(Object.assign(document.createElement('li'), {
      textContent: 'No recordings yet.',
    }));
  }

  /* ------------------------------------------------------------ mutation */

  async deletePhrase(phrase) {
    // Deleting takes the audio with it and cannot be undone, so confirm.
    if (!window.confirm(`Delete "${phrase.text}" and its recordings?`)) return;
    try {
      await requestJSON(`/api/phrases/${phrase.id}`, { method: 'DELETE' }).catch(error => {
        // 204 No Content has no body; api.js treats that as unreadable.
        if (!/unreadable/i.test(error.message)) throw error;
      });
      this.app?.showToast(`Deleted "${phrase.text}"`);
      await this.load();
    } catch (error) {
      this.say(error.message);
    }
  }

  async deleteSample(phrase, sample) {
    try {
      await requestJSON(`/api/phrases/${phrase.id}/samples/${sample.id}`, {
        method: 'DELETE',
      }).catch(error => {
        if (!/unreadable/i.test(error.message)) throw error;
      });
      await this.load();
    } catch (error) {
      this.say(error.message);
    }
  }

  /* -------------------------------------------------------- add a phrase */

  renderEditor() {
    if (!this.editor) return;
    const form = document.createElement('form');
    form.className = 'phrase-form';
    form.noValidate = true;

    const heading = document.createElement('h2');
    heading.textContent = 'Add a phrase';

    const textLabel = document.createElement('label');
    textLabel.htmlFor = 'new-phrase-text';
    textLabel.textContent = 'What do you want to say?';
    const text = document.createElement('input');
    text.id = 'new-phrase-text';
    text.name = 'text';
    text.type = 'text';
    text.required = true;
    text.maxLength = 200;
    text.autocomplete = 'off';
    text.placeholder = 'I want water';

    const emojiLabel = document.createElement('p');
    emojiLabel.className = 'field-label';
    emojiLabel.textContent = 'Pick a picture';
    const emojiRow = document.createElement('div');
    emojiRow.className = 'emoji-row';
    emojiRow.setAttribute('role', 'radiogroup');
    emojiRow.setAttribute('aria-label', 'Pick a picture');
    let chosenEmoji = '';
    for (const emoji of SUGGESTED_EMOJI) {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'emoji-option';
      option.textContent = emoji;
      option.setAttribute('role', 'radio');
      option.setAttribute('aria-checked', 'false');
      option.setAttribute('aria-label', `Picture ${emoji}`);
      option.onclick = () => {
        chosenEmoji = chosenEmoji === emoji ? '' : emoji;
        for (const sibling of emojiRow.children) {
          const active = sibling === option && chosenEmoji === emoji;
          sibling.setAttribute('aria-checked', String(active));
          sibling.classList.toggle('selected', active);
        }
      };
      emojiRow.append(option);
    }

    const photoLabel = document.createElement('label');
    photoLabel.htmlFor = 'new-phrase-image';
    photoLabel.className = 'field-label';
    photoLabel.textContent = 'Or use a photo';
    const photo = document.createElement('input');
    photo.id = 'new-phrase-image';
    photo.type = 'file';
    photo.accept = 'image/*';

    const personalRow = document.createElement('label');
    personalRow.className = 'checkbox-row';
    const personal = document.createElement('input');
    personal.type = 'checkbox';
    personal.id = 'new-phrase-personal';
    personalRow.append(personal, document.createTextNode(' This is my own word (not standard English)'));

    const submit = document.createElement('button');
    submit.type = 'submit';
    submit.className = 'primary-btn';
    submit.textContent = 'Save and record';

    const error = document.createElement('p');
    error.className = 'form-error';
    error.setAttribute('role', 'alert');

    form.onsubmit = async event => {
      event.preventDefault();
      error.textContent = '';
      const value = text.value.trim();
      if (!value) {
        error.textContent = 'Please type the words first.';
        text.focus();
        return;
      }
      submit.disabled = true;
      submit.textContent = 'Saving…';
      try {
        const body = new FormData();
        body.append('text', value);
        if (chosenEmoji) body.append('emoji', chosenEmoji);
        body.append('personal_word', String(personal.checked));
        if (photo.files?.[0]) body.append('image', photo.files[0]);
        const phrase = await requestJSON('/api/phrases', { method: 'POST', body });
        await this.load();
        // Straight into recording: a phrase with no samples cannot be matched.
        this.startRecording(phrase);
      } catch (requestError) {
        error.textContent = requestError.message;
      } finally {
        submit.disabled = false;
        submit.textContent = 'Save and record';
      }
    };

    form.append(
      heading, textLabel, text,
      emojiLabel, emojiRow,
      photoLabel, photo,
      personalRow, submit, error
    );
    this.editor.replaceChildren(form);
  }

  /* ------------------------------------------------------- record samples */

  startRecording(phrase) {
    this.flow?.close();
    this.flow = new SampleRecorderFlow(phrase, {
      onDone: () => {
        this.flow = null;
        this.load();
      },
    });
    this.flow.open();
  }

  cancel() {
    this.flow?.close();
    this.flow = null;
  }
}

/**
 * Record three samples for one phrase, with playback and re-record.
 *
 * Each sample uploads as it is accepted rather than all three at the end, so
 * a dropped connection or a closed tab does not lose the whole set.
 */
export class SampleRecorderFlow {
  constructor(phrase, { onDone, recorder } = {}) {
    this.phrase = phrase;
    this.onDone = onDone || (() => {});
    this.recorder = recorder || new Recorder();
    this.recorded = phrase.sample_count ?? phrase.samples?.length ?? 0;
    this.target = Math.max(SAMPLES_WANTED, this.recorded + 1);
    this.pending = null;
    this.overlay = null;
  }

  open() {
    const overlay = document.createElement('div');
    overlay.className = 'sample-flow-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', `Record ${this.phrase.text}`);

    const display = document.createElement('div');
    display.className = 'phrase-display';
    const emoji = document.createElement('span');
    emoji.className = 'big-emoji';
    emoji.textContent = this.phrase.emoji || '💬';
    const text = document.createElement('p');
    text.className = 'phrase-text';
    text.textContent = this.phrase.text;
    display.append(emoji, text);

    this.progress = document.createElement('p');
    this.progress.className = 'progress';
    this.progress.setAttribute('role', 'status');

    this.recordBtn = document.createElement('button');
    this.recordBtn.type = 'button';
    this.recordBtn.className = 'talk-button';
    this.recordBtn.onclick = () => this.toggle();

    this.controls = document.createElement('div');
    this.controls.className = 'sample-controls';
    this.controls.hidden = true;

    this.playBtn = button('▶︎ Play', () => this.play());
    this.retryBtn = button('🔄 Re-record', () => this.reset());
    this.keepBtn = button('✓ Keep', () => this.keep(), 'primary-btn');
    this.controls.append(this.playBtn, this.retryBtn, this.keepBtn);

    this.message = document.createElement('p');
    this.message.className = 'form-note';
    // role=status, not alert: most of what goes here is an instruction
    // ("Tap to record"), and alert interrupts a screen reader every time.
    // Failures are marked by tone instead, and read on the next pause.
    this.message.setAttribute('role', 'status');

    const done = button('Done for now', () => this.finish());
    done.className = 'text-button';

    overlay.append(display, this.progress, this.recordBtn, this.controls, this.message, done);
    document.body.append(overlay);
    this.overlay = overlay;
    this.update('Tap to record, then tap again to stop.');
    this.recordBtn.focus();
  }

  /**
   * Update the progress line, and optionally the message under it.
   *
   * `tone` exists because this one element carries both instructions and
   * failures. It used to be styled as an error unconditionally, so "Tap to
   * record, then tap again to stop" was shown in red to someone who had
   * done nothing wrong.
   */
  update(message, tone = 'info') {
    const index = Math.min(this.recorded + 1, this.target);
    this.progress.textContent = `Recording ${index} of ${this.target}`;
    this.recordBtn.textContent = this.busy ? 'Stop' : 'Record';
    if (message !== undefined) {
      this.message.textContent = message;
      this.message.className = tone === 'error' ? 'form-error' : 'form-note';
    }
  }

  async toggle() {
    if (this.busy) {
      this.recorder.stop();
      return;
    }
    this.pending = null;
    this.controls.hidden = true;
    this.busy = true;
    this.update('Listening…');
    try {
      const recording = await this.recorder.start(() => this.update('Listening…'));
      this.busy = false;
      this.releasePlayback(); // a new take replaces the old clip
      if (!recording) {
        this.update('That was too short. Try once more.');
        return;
      }
      this.pending = recording;
      this.controls.hidden = false;
      this.update('Play it back, keep it, or record again.');
      this.playBtn.focus();
    } catch (error) {
      this.busy = false;
      this.update(
        error.name === 'NotAllowedError'
          ? 'Microphone access was denied. Allow it in your browser settings.'
          : error.message,
        'error'
      );
    }
  }

  /**
   * One object URL and one <audio> per recording, reused across taps.
   *
   * This used to revoke the previous URL and create a new Audio on every tap.
   * Tapping Play twice quickly revoked the URL the first player was still
   * loading, and Chrome rejected it with "NotSupportedError: no supported
   * source" — reported as "Could not play that back" although the clip was fine.
   */
  play() {
    if (!this.pending) return;
    if (!this.player) {
      this.playbackUrl = URL.createObjectURL(this.pending.blob);
      this.player = new Audio(this.playbackUrl);
    } else {
      this.player.pause();
      this.player.currentTime = 0;
    }
    const { blob, mimeType } = this.pending;
    this.player.play().catch(error => {
      // AbortError just means a newer tap interrupted this one.
      if (error.name === 'AbortError') return;
      console.error('Sample playback failed', { error, mimeType, size: blob.size });
      this.update(`Could not play that back (${error.name}: ${error.message}).`, 'error');
    });
  }

  /** Stop playback and free the clip's URL; called whenever the recording is discarded. */
  releasePlayback() {
    this.player?.pause();
    this.player = null;
    if (this.playbackUrl) {
      URL.revokeObjectURL(this.playbackUrl);
      this.playbackUrl = null;
    }
  }

  reset() {
    this.releasePlayback();
    this.pending = null;
    this.controls.hidden = true;
    this.update('Tap to record again.');
    this.recordBtn.focus();
  }

  async keep() {
    if (!this.pending) return;
    this.keepBtn.disabled = true;
    this.update('Saving…');
    try {
      const body = new FormData();
      const extension = extensionFor(this.pending.mimeType);
      body.append('audio', this.pending.blob, `sample.${extension}`);
      await requestJSON(`/api/phrases/${this.phrase.id}/samples`, { method: 'POST', body });
      this.recorded += 1;
      this.releasePlayback();
      this.pending = null;
      this.controls.hidden = true;
      if (this.recorded >= this.target) {
        this.finish();
        return;
      }
      this.update('Saved. Say it once more.');
      this.recordBtn.focus();
    } catch (error) {
      this.update(error.message, 'error');
    } finally {
      this.keepBtn.disabled = false;
    }
  }

  finish() {
    this.close();
    this.onDone();
  }

  close() {
    this.recorder.cancel();
    this.releasePlayback();
    this.overlay?.remove();
    this.overlay = null;
  }
}

function button(text, onClick, className = 'text-button') {
  const element = document.createElement('button');
  element.type = 'button';
  element.className = className;
  element.textContent = text;
  element.onclick = onClick;
  return element;
}

/** Match talk.js: the server sniffs content type, but a sane name helps. */
export function extensionFor(mimeType = '') {
  if (mimeType.includes('webm')) return 'webm';
  if (mimeType.includes('mp4')) return 'm4a';
  if (mimeType.includes('ogg')) return 'ogg';
  if (mimeType.includes('wav')) return 'wav';
  return 'bin';
}
