/* The calibration page's one talking point with the server.

   Each take goes up whole and is saved as it is: the person read the phrase
   on screen, so nothing needs transcribing. The sound engine lines the take
   up against the phrase's sounds afterwards, in the background. */

const BASE = '/api/calibration/sentences';

export class CalibrationApi {
  constructor({ fetch: fetcher = (...args) => globalThis.fetch(...args) } = {}) {
    this.fetch = fetcher;
  }

  /* The built-in enrollment phrases and how many takes each wants. */
  async enrollmentSet() {
    const response = await this.fetch('/api/calibration/enrollment-set');
    if (!response.ok) throw new Error(await detail(response));
    const body = await response.json();
    if (!Array.isArray(body?.phrases) || !body.phrases.length) throw new Error('No phrases to record.');
    return body;
  }

  async save({ id, take = 1, text, blob, mimeType }) {
    const body = new FormData();
    body.append('sentence_id', id);
    body.append('text', text);
    body.append('take', String(take));
    body.append('audio', blob, `${id}.${extensionFor(mimeType)}`);
    const response = await this.fetch(BASE, { method: 'POST', body });
    if (!response.ok) throw new Error(await detail(response));
    return response.json();
  }

  async list() {
    const response = await this.fetch(BASE);
    if (!response.ok) return [];
    const value = await response.json();
    return Array.isArray(value) ? value : [];
  }

  async remove(id, take = null) {
    const query = take ? `?take=${take}` : '';
    const response = await this.fetch(`${BASE}/${encodeURIComponent(id)}${query}`, { method: 'DELETE' });
    return response.ok;
  }

  audioUrl(id, take = 1) {
    return `${BASE}/${encodeURIComponent(id)}/audio${take > 1 ? `?take=${take}` : ''}`;
  }
}

export function extensionFor(mimeType = '') {
  const base = String(mimeType).split(';')[0].trim().toLowerCase();
  return { 'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a', 'audio/wav': 'wav' }[base] || 'webm';
}

async function detail(response) {
  try {
    const body = await response.json();
    if (typeof body?.detail === 'string') return body.detail;
  } catch { /* not JSON */ }
  return 'That one didn’t save. Nothing is lost — try it again.';
}
