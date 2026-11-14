/**
 * Correction flow (Task 11).
 *
 * Opened from "No" or "None of these". Offers, in the PRD's order: the other
 * top-3 candidates, the full phrase grid, typing what was said, and Skip.
 * Resolves to a feedback label:
 *
 *   {type: 'phrase', phrase_id}  a saved phrase
 *   {type: 'text', text}         typed by a supporter
 *   null                         Skip: negative signal only
 *
 * and resolves to `undefined` if the user backs out, which stores nothing.
 *
 * Two deliberate departures from the task's code sketch:
 *
 *  - It builds nodes instead of assigning `innerHTML` with interpolated
 *    phrase text. Phrase text is user input and goes straight into the DOM;
 *    `textContent` keeps a phrase containing `<` from breaking the grid.
 *  - Typing uses an inline form, not `window.prompt`. A prompt is a small
 *    fixed-size dialog with no large targets, it is suppressed in some
 *    mobile browsers, and the PRD's users are described as needing large
 *    touch targets and high contrast.
 */

import { requestJSON } from './api.js';

export class CorrectionFlow {
  constructor({ loadPhrases } = {}) {
    this.loadPhrases = loadPhrases || (() => requestJSON('/api/phrases'));
    this.overlay = null;
    this.resolve = null;
    this.previouslyFocused = null;
  }

  /**
   * Show the overlay. Returns a promise for the chosen label.
   * @param {Array} otherCandidates candidates to offer again (the rejected one excluded)
   */
  open({ otherCandidates = [] } = {}) {
    if (this.overlay) this.close(undefined);
    this.previouslyFocused = document.activeElement;

    const overlay = document.createElement('div');
    overlay.className = 'correction-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'What did you say?');

    const heading = document.createElement('h2');
    heading.className = 'correction-header';
    heading.textContent = 'What did you say?';
    overlay.append(heading);

    if (otherCandidates.length) {
      overlay.append(
        this.section('Maybe one of these?', otherCandidates.map(candidate =>
          this.choiceButton(candidate, () => this.finish(labelFor(candidate)))
        ))
      );
    }

    const grid = document.createElement('div');
    grid.className = 'phrase-grid';
    grid.append(loadingNote('Loading your phrases…'));
    overlay.append(this.section('All my phrases', [grid]));

    overlay.append(this.typeSection());

    const skip = document.createElement('button');
    skip.type = 'button';
    skip.className = 'correction-skip-btn';
    skip.textContent = 'Skip';
    skip.onclick = () => this.finish(null);

    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'correction-cancel-btn';
    cancel.textContent = 'Go back';
    // Backing out stores nothing at all, which is different from Skip.
    cancel.onclick = () => this.finish(undefined);

    overlay.append(skip, cancel);

    overlay.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        this.finish(undefined);
      }
    });

    document.body.append(overlay);
    this.overlay = overlay;
    heading.tabIndex = -1;
    heading.focus();

    this.fillGrid(grid);
    return new Promise(resolve => {
      this.resolve = resolve;
    });
  }

  async fillGrid(grid) {
    try {
      const phrases = await this.loadPhrases();
      if (!this.overlay?.contains(grid)) return;
      grid.replaceChildren();
      if (!Array.isArray(phrases) || phrases.length === 0) {
        grid.append(loadingNote('No saved phrases yet.'));
        return;
      }
      for (const phrase of phrases) {
        grid.append(
          this.choiceButton(phrase, () =>
            this.finish({ type: 'phrase', phrase_id: phrase.id })
          , 'phrase-grid-btn')
        );
      }
    } catch (error) {
      if (!this.overlay?.contains(grid)) return;
      grid.replaceChildren(loadingNote(error.message));
    }
  }

  section(title, children) {
    const section = document.createElement('section');
    section.className = 'correction-section';
    const heading = document.createElement('h3');
    heading.textContent = title;
    section.append(heading, ...children);
    return section;
  }

  choiceButton(item, onClick, className = 'correction-btn') {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;

    const icon = document.createElement('span');
    icon.className = 'icon';
    if (item.image_url || item.image) {
      const img = document.createElement('img');
      img.src = item.image_url || item.image;
      img.alt = '';
      icon.append(img);
    } else {
      icon.textContent = item.emoji || '💬';
    }

    const text = document.createElement('span');
    text.className = 'text';
    text.textContent = item.text;

    button.append(icon, text);
    button.onclick = onClick;
    return button;
  }

  typeSection() {
    const form = document.createElement('form');
    form.className = 'correction-type';

    const label = document.createElement('label');
    label.htmlFor = 'correction-type-input';
    label.textContent = '✏️ Type what I said';

    const input = document.createElement('input');
    input.id = 'correction-type-input';
    input.type = 'text';
    input.autocomplete = 'off';
    input.maxLength = 500;
    input.placeholder = 'Type the words here';

    const submit = document.createElement('button');
    submit.type = 'submit';
    submit.className = 'correction-type-btn';
    submit.textContent = 'Use this';

    form.onsubmit = event => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) {
        input.focus();
        return;
      }
      this.finish({ type: 'text', text });
    };

    form.append(label, input, submit);
    return form;
  }

  finish(label) {
    const resolve = this.resolve;
    this.close();
    resolve?.(label);
  }

  close() {
    this.overlay?.remove();
    this.overlay = null;
    this.resolve = null;
    // Return focus so a keyboard or switch user is not dropped at the top.
    if (this.previouslyFocused?.isConnected) this.previouslyFocused.focus();
    this.previouslyFocused = null;
  }
}

/** A feedback label for a recognition candidate. */
export function labelFor(candidate) {
  if (!candidate) return null;
  return candidate.type === 'phrase' && candidate.phrase_id
    ? { type: 'phrase', phrase_id: candidate.phrase_id }
    : { type: 'text', text: candidate.text };
}

function loadingNote(message) {
  const note = document.createElement('p');
  note.className = 'grid-note';
  note.textContent = message;
  return note;
}
