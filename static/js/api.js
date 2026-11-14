export async function requestJSON(path, options = {}) {
  const controller = new AbortController();
  const parent = options.signal;
  const abort = () => controller.abort();
  if (parent?.aborted) controller.abort();
  parent?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(abort, 30000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : 'Could not complete that request. Please try again.';
      throw new Error(detail);
    }
    if (data === null) throw new Error('The server sent an unreadable response.');
    return data;
  } catch (error) {
    if (error.name === 'AbortError' && !parent?.aborted) throw new Error('The request took too long. Please try again.');
    if (error instanceof TypeError) throw new Error('Could not reach Heard. Check your connection and try again.');
    throw error;
  } finally {
    clearTimeout(timeout);
    parent?.removeEventListener('abort', abort);
  }
}

export function postJSON(path, body) {
  return requestJSON(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
