/* The calibration page's one talking point with the server.

   A sentence goes up whole; the server cuts it into word clips using the
   speech-to-text word timings and hands back what it managed to place. */

const BASE = '/api/calibration/sentences';

export class CalibrationApi {
  constructor({ fetch: fetcher = (...args) => globalThis.fetch(...args) } = {}) {
    this.fetch = fetcher;
  }

  async save({ id, text, blob, mimeType }) {
    const body = new FormData();
    body.append('sentence_id', id);
    body.append('text', text);
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

  async remove(id) {
    const response = await this.fetch(`${BASE}/${encodeURIComponent(id)}`, { method: 'DELETE' });
    return response.ok;
  }

  audioUrl(id) {
    return `${BASE}/${encodeURIComponent(id)}/audio`;
  }

  wordUrl(id, index) {
    return `${BASE}/${encodeURIComponent(id)}/words/${index}`;
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
