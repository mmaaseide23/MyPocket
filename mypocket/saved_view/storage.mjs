// Personal saved-view storage; no automatic database access at module load.
export class SnapshotError extends Error {
  constructor(code) { super(code); this.code = code; }
}
export class SavedViewStore {
  constructor(indexedDB = globalThis.indexedDB) { this.indexedDB = indexedDB; }
  async database() {
    if (!this.indexedDB) throw new SnapshotError('storage_unavailable');
    return new Promise((resolve, reject) => {
      const request = this.indexedDB.open('mypocket-personal-saved-view-v1', 1);
      let blocked = false;
      request.onupgradeneeded = () => request.result.createObjectStore('saved');
      request.onerror = () => reject(new SnapshotError('storage_unavailable'));
      request.onblocked = () => { blocked = true; reject(new SnapshotError('storage_unavailable')); };
      request.onsuccess = () => {
        if (blocked) { request.result.close(); return; }
        request.result.onversionchange = () => request.result.close(); resolve(request.result);
      };
    });
  }
  async transaction(mode, action) {
    const db = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction('saved', mode), store = transaction.objectStore('saved'); let result;
      transaction.oncomplete = () => { db.close(); resolve(result); };
      transaction.onerror = transaction.onabort = () => { db.close(); reject(new SnapshotError('storage_unavailable')); };
      try { action(store, value => { result = value; }); } catch { transaction.abort(); }
    });
  }
  read() {
    return this.transaction('readonly', (store, finish) => {
      const revision = store.get('revision'), record = store.get('snapshot');
      record.onsuccess = () => finish({revision: revision.result || 'initial', record: record.result || null});
    });
  }
  write(record, revision) {
    return this.transaction('readwrite', (store, finish) => {
      const current = store.get('revision');
      current.onsuccess = () => {
        if ((current.result || 'initial') !== revision) { finish(false); return; }
        store.put(record, 'snapshot'); finish(true);
      };
    });
  }
  clear() {
    return this.transaction('readwrite', (store, finish) => {
      store.delete('snapshot'); store.put(globalThis.crypto.randomUUID(), 'revision'); finish(true);
    });
  }
}
