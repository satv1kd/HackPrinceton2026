/* Local persistence for calibration recordings.

   IndexedDB, one record per sentence, behind a small interface so a backend
   can replace it later without touching the calibration flow:

     { id, text, blob, mimeType, durationMs, timestamps, recordedAt, skipped }

   `timestamps` is the word-level timing from whichever progress strategy was
   driving: [{ word, index, atMs, source }]. Falls back to an in-memory Map
   where IndexedDB is unavailable (private windows, jsdom). */

const DB_NAME = 'heard-calibration';
const STORE = 'sentences';

export class CalibrationStore {
  constructor(dbName = DB_NAME) {
    this.dbName = dbName;
    this.memory = null; // fallback
    this.dbPromise = null;
  }

  _open() {
    if (this.memory) return null;
    if (!globalThis.indexedDB) { this.memory = new Map(); return null; }
    if (!this.dbPromise) {
      this.dbPromise = new Promise((resolve, reject) => {
        const request = indexedDB.open(this.dbName, 1);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains(STORE)) {
            request.result.createObjectStore(STORE, { keyPath: 'id' });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      }).catch(error => {
        console.warn('IndexedDB unavailable; keeping recordings in memory', error);
        this.memory = new Map();
        return null;
      });
    }
    return this.dbPromise;
  }

  async _tx(mode, run) {
    const db = await this._open();
    if (!db) return run(null);
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const request = run(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(request?.result);
      tx.onerror = () => reject(tx.error);
    });
  }

  async save(entry) {
    if (this.memory || !(await this._open())) { this.memory.set(entry.id, entry); return entry; }
    await this._tx('readwrite', store => store.put(entry));
    return entry;
  }

  async get(id) {
    if (this.memory || !(await this._open())) return this.memory.get(id) ?? null;
    return (await this._tx('readonly', store => store.get(id))) ?? null;
  }

  async list() {
    if (this.memory || !(await this._open())) return [...this.memory.values()];
    return (await this._tx('readonly', store => store.getAll())) ?? [];
  }

  async delete(id) {
    if (this.memory || !(await this._open())) { this.memory.delete(id); return; }
    await this._tx('readwrite', store => store.delete(id));
  }

  async clear() {
    if (this.memory || !(await this._open())) { this.memory.clear(); return; }
    await this._tx('readwrite', store => store.clear());
  }
}
