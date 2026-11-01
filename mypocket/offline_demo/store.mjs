// Fictional-only offline storage. No provider tokens, cookies or live API payloads.
export const SCOPE = 'fictional-owner-v1';
export const RETENTION_MS = 24 * 60 * 60 * 1000;
export const ITERATIONS = 600000;
const encoder = new TextEncoder();
const decoder = new TextDecoder();

export class SnapshotError extends Error {
  constructor(code) { super(code); this.code = code; }
}
const fail = code => { throw new SnapshotError(code); };
const exact = (value, names) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('|') === [...names].sort().join('|');
const date = value => typeof value === 'string' && /Z$/.test(value) && Number.isFinite(Date.parse(value));
const label = value => typeof value === 'string' && value.length > 0 && value.length <= 64;

export function validateSnapshot(value, scope = SCOPE) {
  if (!exact(value, ['version', 'fictional', 'owner_scope', 'captured_at', 'currency', 'accounts', 'sources'])
      || value.version !== 1 || value.fictional !== true || value.owner_scope !== scope
      || value.currency !== 'USD' || !date(value.captured_at)
      || !Array.isArray(value.accounts) || value.accounts.length < 1 || value.accounts.length > 20
      || !Array.isArray(value.sources) || value.sources.length < 1 || value.sources.length > 10) fail('invalid_snapshot');
  for (const account of value.accounts) {
    if (!exact(account, ['label', 'type', 'balance']) || !label(account.label)
        || !['checking', 'savings', 'brokerage', 'ira', 'credit'].includes(account.type)
        || typeof account.balance !== 'number' || !Number.isFinite(account.balance)
        || Math.abs(account.balance) > 1e12) fail('invalid_snapshot');
  }
  for (const source of value.sources) {
    if (!exact(source, ['label', 'last_successful_check_at', 'provider_as_of']) || !label(source.label)
        || !(source.last_successful_check_at === null || date(source.last_successful_check_at))
        || !(source.provider_as_of === null || date(source.provider_as_of))) fail('invalid_snapshot');
  }
  return value;
}

function base64(bytes) { return btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join('')); }
function bytes(text, length) {
  if (typeof text !== 'string' || text.length > 180000 || !/^[A-Za-z0-9+/]*={0,2}$/.test(text)) fail('invalid_record');
  let result;
  try { result = Uint8Array.from(atob(text), character => character.charCodeAt(0)); }
  catch { fail('invalid_record'); }
  if (length && result.length !== length) fail('invalid_record');
  return result;
}
function aad(record) {
  return encoder.encode(JSON.stringify([record.version, record.scope, record.saved_at, record.expires_at, record.iterations]));
}
function metadata(record, scope, now) {
  if (!exact(record, ['version', 'scope', 'saved_at', 'expires_at', 'iterations', 'salt', 'iv', 'ciphertext'])
      || record.version !== 1 || record.iterations !== ITERATIONS || !date(record.saved_at) || !date(record.expires_at)) fail('invalid_record');
  if (record.scope !== scope) fail('wrong_owner');
  const saved = Date.parse(record.saved_at), expires = Date.parse(record.expires_at);
  if (expires - saved !== RETENTION_MS || saved > now + 300000) fail('invalid_record');
  if (expires <= now) fail('expired');
  bytes(record.salt, 16); bytes(record.iv, 12); bytes(record.ciphertext);
}
async function derive(phrase, salt, crypto) {
  if (typeof phrase !== 'string' || phrase.length < 12 || phrase.length > 256) fail('phrase_length');
  if (!crypto?.subtle) fail('crypto_unavailable');
  const material = await crypto.subtle.importKey('raw', encoder.encode(phrase), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({name: 'PBKDF2', hash: 'SHA-256', salt, iterations: ITERATIONS}, material,
    {name: 'AES-GCM', length: 256}, false, ['encrypt', 'decrypt']);
}

export async function sealSnapshot(snapshot, phrase, {scope = SCOPE, now = Date.now(), crypto = globalThis.crypto} = {}) {
  validateSnapshot(snapshot, scope);
  if (Date.parse(snapshot.captured_at) > now + 300000) fail('invalid_snapshot');
  if (!crypto?.subtle) fail('crypto_unavailable');
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
  const record = {version: 1, scope, saved_at: new Date(now).toISOString(),
    expires_at: new Date(now + RETENTION_MS).toISOString(), iterations: ITERATIONS,
    salt: base64(salt), iv: base64(iv), ciphertext: ''};
  const key = await derive(phrase, salt, crypto);
  const encrypted = await crypto.subtle.encrypt({name: 'AES-GCM', iv, additionalData: aad(record)}, key,
    encoder.encode(JSON.stringify(snapshot)));
  record.ciphertext = base64(new Uint8Array(encrypted));
  return record;
}

export async function openSnapshot(record, phrase, {scope = SCOPE, now = Date.now(), crypto = globalThis.crypto} = {}) {
  metadata(record, scope, now);
  const key = await derive(phrase, bytes(record.salt, 16), crypto);
  let snapshot;
  try {
    const plain = await crypto.subtle.decrypt({name: 'AES-GCM', iv: bytes(record.iv, 12), additionalData: aad(record)},
      key, bytes(record.ciphertext));
    snapshot = JSON.parse(decoder.decode(plain));
  } catch { fail('unlock_failed'); }
  return validateSnapshot(snapshot, scope);
}

export class IndexedSnapshotStore {
  constructor(indexedDB = globalThis.indexedDB) { this.indexedDB = indexedDB; }
  async database() {
    if (!this.indexedDB) fail('storage_unavailable');
    return new Promise((resolve, reject) => {
      const request = this.indexedDB.open('mypocket-fictional-offline-v1', 1);
      let blocked = false;
      request.onupgradeneeded = () => request.result.createObjectStore('saved');
      request.onsuccess = () => {
        if (blocked) { request.result.close(); return; }
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
      request.onerror = () => reject(new SnapshotError('storage_unavailable'));
      request.onblocked = () => { blocked = true; reject(new SnapshotError('storage_unavailable')); };
    });
  }
  async transaction(mode, action) {
    const db = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction('saved', mode), store = transaction.objectStore('saved');
      let result;
      transaction.oncomplete = () => { db.close(); resolve(result); };
      transaction.onerror = transaction.onabort = () => { db.close(); reject(new SnapshotError('storage_unavailable')); };
      try { action(store, value => { result = value; }); }
      catch { transaction.abort(); }
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

export class SnapshotVault {
  constructor(store, {scope = SCOPE, now = Date.now, crypto = globalThis.crypto, onLock = () => {}} = {}) {
    this.store = store; this.scope = scope; this.now = now; this.crypto = crypto;
    this.generation = 0; this.snapshot = null; this.record = null; this.onLock = onLock;
  }
  lock() { this.generation++; this.snapshot = this.record = null; this.onLock(); }
  async forget() { this.lock(); await this.store.clear(); }
  async status() {
    const {record} = await this.store.read();
    if (!record) return 'empty';
    try { metadata(record, this.scope, this.now()); return 'locked'; }
    catch (error) { await this.forget(); return error.code === 'expired' ? 'expired' : 'empty'; }
  }
  async assertCurrent() {
    const {record} = await this.store.read();
    if (!this.record || record?.ciphertext !== this.record.ciphertext) { this.lock(); fail('cancelled'); }
    try { metadata(record, this.scope, this.now()); }
    catch (error) { await this.forget(); throw error; }
  }
  async save(snapshot, phrase) {
    this.lock(); const generation = this.generation;
    const {revision} = await this.store.read();
    const record = await sealSnapshot(snapshot, phrase, {scope: this.scope, now: this.now(), crypto: this.crypto});
    if (generation !== this.generation) fail('cancelled');
    if (!await this.store.write(record, revision)) fail('cancelled');
    if (generation !== this.generation) { await this.store.clear(); fail('cancelled'); }
    return record.saved_at;
  }
  async unlock(phrase) {
    this.lock(); const generation = this.generation;
    const loaded = await this.store.read();
    if (!loaded.record) fail('empty');
    let snapshot;
    try { snapshot = await openSnapshot(loaded.record, phrase, {scope: this.scope, now: this.now(), crypto: this.crypto}); }
    catch (error) {
      if (['expired', 'wrong_owner', 'invalid_record', 'invalid_snapshot'].includes(error.code)) await this.forget();
      throw error;
    }
    const current = await this.store.read();
    if (generation !== this.generation || current.revision !== loaded.revision
        || current.record?.ciphertext !== loaded.record.ciphertext) fail('cancelled');
    this.snapshot = snapshot; this.record = loaded.record;
    return snapshot;
  }
}

export async function checkAccess(fetcher = globalThis.fetch, scope = SCOPE) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetcher('/offline-demo/access', {cache: 'no-store', credentials: 'omit', redirect: 'manual', signal: controller.signal});
    if ([401, 403].includes(response.status) || response.redirected || response.type === 'opaqueredirect'
        || (response.status >= 300 && response.status < 400)) return 'denied';
    if (!response.ok) return 'unavailable';
    let access;
    try { access = await response.json(); } catch { return 'denied'; }
    return access?.fictional === true && access.active === true && access.owner_scope === scope ? 'allowed' : 'denied';
  } catch { return 'unavailable'; }
  finally { clearTimeout(timer); }
}
