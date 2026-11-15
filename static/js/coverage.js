/**
 * "Your sounds" in Setup (Task 32): what Heard has learned about how you say
 * each sound, from the calibration phrases and from your Yes/No answers.
 *
 * Plain words first ("R as in red"); the phonetic symbol is shown small, for
 * supporters. Nothing here grades the speaker: a sound with few examples
 * "needs more examples", it is never "wrong", and learned patterns are
 * described as what Heard now listens for.
 */

import { requestJSON } from './api.js';

const STATUS_TEXT = {
  learned: 'Learned',
  needs_more: 'Needs more examples',
  not_yet: 'Not recorded yet',
};

export class SoundCoverage {
  constructor(container, { request = requestJSON } = {}) {
    this.container = container;
    this.request = request;
    this.loading = null;
    document.addEventListener('heard:feedback', () => this.load());
  }

  load() {
    this.loading ??= this.fetchAndRender().finally(() => { this.loading = null; });
    return this.loading;
  }

  async fetchAndRender() {
    let profile;
    try {
      profile = await this.request('/api/profile');
    } catch {
      profile = null;
    }
    if (!Array.isArray(profile?.sounds)) {
      this.container.hidden = true;   // an older server, the engine off, or a bad reply
      return;
    }
    let phrases = [];
    try {
      const set = await this.request('/api/calibration/enrollment-set');
      if (Array.isArray(set?.phrases)) phrases = set.phrases;
    } catch { /* suggestions are optional */ }
    this.render(profile, phrases);
  }

  render(profile, phrases = []) {
    this.container.hidden = false;
    const heading = el('h2', 'coverage-heading', 'Your sounds');
    heading.id = 'coverage-heading';
    const parts = [heading, this.summary(profile)];

    const patterns = (profile.patterns || []).filter(p => p.probability >= 0.3).slice(0, 3);
    if (patterns.length) {
      const list = el('ul', 'coverage-patterns');
      for (const p of patterns) {
        const sound = profile.sounds.find(s => s.phoneme === p.intended);
        const heard = profile.sounds.find(s => s.phoneme === p.heard);
        const text = p.heard === '<DEL>'
          ? `When you say ${label(sound)}, it is often soft or quiet — Heard now listens for that.`
          : `When you say ${label(sound)}, it often comes through like ${label(heard)} — Heard now listens for that.`;
        list.append(el('li', '', text));
      }
      parts.push(list);
    }

    for (const [title, vowel] of [['Consonants', false], ['Vowels', true]]) {
      const group = el('div', 'coverage-group');
      group.append(el('h3', '', title));
      const grid = el('ul', 'coverage-grid');
      for (const sound of profile.sounds.filter(s => s.vowel === vowel)) grid.append(this.tile(sound, profile));
      group.append(grid);
      parts.push(group);
    }

    const weak = profile.sounds.filter(s => s.status !== 'learned').map(s => s.phoneme);
    if (weak.length) parts.push(this.suggestion(weak, phrases, profile));
    this.container.replaceChildren(...parts);
  }

  summary(profile) {
    const p = el('p', 'coverage-summary');
    const learned = profile.sounds.filter(s => s.status === 'learned').length;
    const bits = [`${learned} of ${profile.sounds.length} sounds learned.`];
    if (profile.version) {
      const takes = profile.sources?.take || 0;
      const answers = profile.sources?.feedback || 0;
      bits.push(`Built from ${takes} calibration recording${takes === 1 ? '' : 's'}` +
        (answers ? ` and ${answers} confirmed answer${answers === 1 ? '' : 's'}.` : '.'));
    }
    const pending = profile.sounds.filter(s => s.pending > 0).sort((a, b) => b.pending - a.pending)[0];
    if (pending) {
      bits.push(`Heard updates after ${profile.retrain_after} new examples of a sound — closest: ` +
        `${label(pending)}, ${pending.pending} of ${profile.retrain_after}.`);
    }
    if (profile.last_retrain && profile.last_retrain.promoted === false) {
      bits.push('The last update didn’t help, so Heard kept the version before it.');
    }
    if (!profile.ready && profile.reason) bits.push(profile.reason);
    p.textContent = bits.join(' ');
    return p;
  }

  tile(sound, profile) {
    const li = el('li', `coverage-tile ${sound.status}`);
    li.append(el('span', 'coverage-word', sound.example));
    li.append(el('span', 'coverage-ipa', `/${sound.ipa}/`));
    li.append(el('span', 'coverage-status', STATUS_TEXT[sound.status] || sound.status));
    if (sound.pending > 0) {
      const bar = el('span', 'coverage-progress');
      bar.style.setProperty('--progress', `${Math.min(100, Math.round(100 * sound.pending / profile.retrain_after))}%`);
      bar.setAttribute('aria-hidden', 'true');
      li.append(bar);
    }
    const name = label(sound);
    li.setAttribute('aria-label', `${name[0].toUpperCase()}${name.slice(1)}: ${STATUS_TEXT[sound.status] || sound.status}, ` +
      `${sound.examples} example${sound.examples === 1 ? '' : 's'}` +
      (sound.pending ? `, ${sound.pending} new since the last update` : ''));
    return li;
  }

  suggestion(weak, phrases, profile) {
    const box = el('div', 'coverage-suggest');
    const helpful = phrases.filter(ph => (ph.phonemes || []).some(p => weak.includes(p))).slice(0, 3);
    const text = helpful.length
      ? `To fill in the rest, record phrases like ${helpful.map(p => `“${p.text}”`).join(', ')}.`
      : 'Recording the calibration phrases fills in the rest.';
    box.append(el('p', '', text));
    const link = el('a', 'text-button', 'Record calibration phrases');
    link.href = '/calibrate';
    box.append(link);
    return box;
  }
}

/* How a sound is named in sentences: "the r sound in “red”", "the vowel in “see”". */
const LETTERS = { HH: 'h', JH: 'j', DH: 'th', Y: 'y' };

export function label(sound) {
  if (!sound) return 'a sound';
  if (sound.vowel) return `the vowel in “${sound.example}”`;
  const letters = LETTERS[sound.phoneme] || sound.phoneme.toLowerCase();
  return `the ${letters} sound in “${sound.example}”`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
