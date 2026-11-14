import { TalkMode } from './talk.js';
import { requestJSON } from './api.js';

export class HeardApp {
  constructor() {
    this.mode = 'talk';
    this.talk = new TalkMode(this);
    for (const mode of ['talk', 'setup']) {
      document.getElementById(`${mode}-tab`).onclick = () => this.switchMode(mode);
      document.getElementById(`${mode}-tab`).onkeydown = event => {
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          const next = event.key === 'Home' ? 'talk' : event.key === 'End' ? 'setup' : mode === 'talk' ? 'setup' : 'talk';
          this.switchMode(next);
          document.getElementById(`${next}-tab`).focus();
        }
      };
    }
  }

  switchMode(mode) {
    if (mode === this.mode) return;
    this.talk.cancel();
    this.mode = mode;
    for (const name of ['talk', 'setup']) {
      document.getElementById(`${name}-mode`).hidden = name !== mode;
      const tab = document.getElementById(`${name}-tab`);
      tab.setAttribute('aria-selected', String(name === mode));
      tab.tabIndex = name === mode ? 0 : -1;
    }
    if (mode === 'setup') this.loadPhrases();
    document.dispatchEvent(new CustomEvent('heard:modechange', { detail: { mode } }));
  }

  async loadPhrases() {
    const status = document.getElementById('setup-status');
    const list = document.getElementById('phrase-list');
    status.textContent = 'Loading your phrases…';
    list.replaceChildren();
    try {
      const phrases = await requestJSON('/api/phrases');
      if (!Array.isArray(phrases)) throw new Error('Could not read your phrases.');
      status.textContent = phrases.length ? '' : 'No saved phrases yet.';
      for (const phrase of phrases) {
        const item = document.createElement('article');
        item.className = 'phrase-item';
        item.textContent = `${phrase.emoji || '💬'} ${phrase.text}`;
        const count = document.createElement('small');
        count.textContent = `${phrase.samples?.length ?? phrase.sample_count ?? 0} recordings`;
        item.append(count);
        list.append(item);
      }
    } catch (error) { status.textContent = error.message; }
  }

  showToast(message, duration = 5000, onUndo = null) {
    clearTimeout(this.toastTimer);
    const toast = document.getElementById('toast');
    const action = document.getElementById('toast-action');
    document.getElementById('toast-message').textContent = message;
    action.hidden = !onUndo;
    action.disabled = false;
    action.onclick = async () => {
      action.disabled = true;
      try { await onUndo(); toast.hidden = true; }
      catch (error) { document.getElementById('toast-message').textContent = error.message; action.disabled = false; }
    };
    toast.hidden = false;
    this.toastTimer = setTimeout(() => { toast.hidden = true; }, Math.max(0, duration));
  }
}

window.app = new HeardApp();
