/**
 * Phonetic mode switch and "Listening for" indicator (Task 21).
 *
 * Phonetic mode adds the user's own sound profile to recognition, between
 * saved-phrase matching and the speech-to-text fallback:
 *
 *   off: your phrases -> best guess (speech-to-text)
 *   on:  your phrases -> your sounds -> best guess
 *
 * The server stores the switch (`/api/preferences`) and reports whether the
 * mode can actually run yet. The switch stays usable while it cannot - the
 * choice is remembered - but the reason is always shown next to it.
 *
 * Every change is announced with a `heard:preferences` event so Setup and
 * Talk stay in step without knowing about each other.
 */

import { requestJSON } from './api.js';

const DEFAULTS = {
  phonetic_mode: false,
  phonetic_available: false,
  phonetic_unavailable_reason: null,
  stt_available: false,
};

/** Accept only the fields we know, with the right types; anything else is ignored. */
export function normalizePreferences(data) {
  const out = { ...DEFAULTS };
  if (!data || typeof data !== 'object' || Array.isArray(data)) return out;
  for (const key of ['phonetic_mode', 'phonetic_available', 'stt_available']) {
    if (typeof data[key] === 'boolean') out[key] = data[key];
  }
  if (typeof data.phonetic_unavailable_reason === 'string') out.phonetic_unavailable_reason = data.phonetic_unavailable_reason;
  return out;
}

export class PreferencesClient {
  constructor({ request = requestJSON, target = document } = {}) {
    this.request = request;
    this.target = target;
    this.state = { ...DEFAULTS };
    this.loaded = false;
  }

  async load() {
    try {
      this.state = normalizePreferences(await this.request('/api/preferences'));
      this.loaded = true;
    } catch {
      // Keep defaults: the app works without preferences (mode off).
    }
    this.emit();
    return this.state;
  }

  /**
   * Optimistic: the switch moves immediately, and moves back if the server
   * refuses. Rejects with the server's error so the caller can tell the user.
   */
  async setPhoneticMode(on) {
    const previous = this.state;
    this.state = { ...previous, phonetic_mode: on };
    this.emit();
    try {
      const saved = await this.request('/api/preferences', {
        method: 'PUT',
        body: JSON.stringify({ phonetic_mode: on }),
        headers: { 'Content-Type': 'application/json' },
      });
      this.state = normalizePreferences(saved);
      this.emit();
      return this.state;
    } catch (error) {
      this.state = previous;
      this.emit();
      throw error;
    }
  }

  emit() {
    this.target.dispatchEvent(new CustomEvent('heard:preferences', { detail: { ...this.state } }));
  }
}

/** Plain-language description of the recognizers that will run. */
export function listeningSummary(state) {
  const parts = ['your phrases'];
  if (state.phonetic_mode && state.phonetic_available) parts.push('your sounds');
  if (state.stt_available) parts.push('best guess');
  let text = `Listening for: ${parts.join(' · ')}`;
  if (state.phonetic_mode && !state.phonetic_available) text += ' (sound learning isn’t ready yet)';
  return text;
}

/**
 * The "Learn from my sounds" switch, rendered into `container`.
 * `onEnroll`, when given, adds a button to the sound-set recording flow
 * (Task 24) next to the "not ready" reason.
 */
export class PhoneticToggle {
  constructor(container, preferences, { notify = () => {}, onEnroll = null } = {}) {
    this.container = container;
    this.preferences = preferences;
    this.notify = notify;
    this.onEnroll = onEnroll;
    this.busy = false;
    this.build();
    document.addEventListener('heard:preferences', event => this.update(event.detail));
    this.update(preferences.state);
  }

  build() {
    const heading = document.createElement('h2');
    heading.className = 'listening-heading';
    heading.id = 'listening-heading';
    heading.textContent = 'How Heard listens';

    this.switch = document.createElement('button');
    this.switch.type = 'button';
    this.switch.className = 'switch';
    this.switch.id = 'phonetic-switch';
    this.switch.setAttribute('role', 'switch');
    this.switch.setAttribute('aria-describedby', 'phonetic-switch-help phonetic-switch-reason');
    const track = document.createElement('span');
    track.className = 'switch-track';
    track.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.className = 'switch-label';
    label.textContent = 'Learn from my sounds';
    this.switch.append(track, label);
    this.switch.onclick = () => this.toggle();

    const help = document.createElement('p');
    help.id = 'phonetic-switch-help';
    help.className = 'form-note';
    help.textContent = 'When on, Heard also uses how you say each sound to understand words you haven’t recorded. Guesses are always shown for you to confirm first.';

    this.reason = document.createElement('p');
    this.reason.id = 'phonetic-switch-reason';
    this.reason.className = 'form-note switch-reason';
    this.reason.setAttribute('role', 'status');

    this.enrollButton = null;
    if (this.onEnroll) {
      this.enrollButton = document.createElement('button');
      this.enrollButton.type = 'button';
      this.enrollButton.className = 'text-button';
      this.enrollButton.textContent = 'Record the sound set';
      this.enrollButton.onclick = () => this.onEnroll();
    }

    this.container.replaceChildren(heading, this.switch, help, this.reason, ...(this.enrollButton ? [this.enrollButton] : []));
  }

  update(state) {
    this.switch.setAttribute('aria-checked', String(state.phonetic_mode));
    this.switch.classList.toggle('on', state.phonetic_mode);
    const notReady = !state.phonetic_available;
    this.reason.hidden = !notReady;
    this.reason.textContent = notReady ? (state.phonetic_unavailable_reason || 'Sound learning isn’t ready yet.') : '';
    if (this.enrollButton) this.enrollButton.hidden = !notReady;
  }

  async toggle() {
    if (this.busy) return;
    this.busy = true;
    this.switch.setAttribute('aria-busy', 'true');
    const next = !this.preferences.state.phonetic_mode;
    try {
      await this.preferences.setPhoneticMode(next);
    } catch (error) {
      this.notify(`Couldn’t change that setting: ${error.message}`);
    } finally {
      this.busy = false;
      this.switch.removeAttribute('aria-busy');
    }
  }
}

/** Keep the Talk screen's indicator in step with the preferences. */
export function bindListeningIndicator(element, preferences) {
  const render = state => { element.textContent = listeningSummary(state); };
  document.addEventListener('heard:preferences', event => render(event.detail));
  render(preferences.state);
}
