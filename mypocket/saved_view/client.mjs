// Disabled, unmounted integration library. The existing fictional demo is unchanged.
// No provider tokens or app passwords belong in this protocol.
import {SnapshotError} from './storage.mjs';
export {SavedViewStore, SnapshotError} from './storage.mjs';

const encoder = new TextEncoder(), decoder = new TextDecoder();
const ITERATIONS = 600000, MAX_MINOR = 100000000000000;
const LABELS = {checking: 'Checking', savings: 'Savings', brokerage: 'Investments', ira: 'Retirement', credit: 'Credit'};
const fail = code => { throw new SnapshotError(code); };
const exact = (v, keys) => v && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).sort().join('|') === [...keys].sort().join('|');
const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(v);
const date = v => typeof v === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(v) && Number.isFinite(Date.parse(v));
const duration = v => Number.isInteger(v) && v >= 3600 && v <= 604800;
const binding = v => uuid(v.owner_scope) && Number.isSafeInteger(v.generation) && v.generation > 0 && duration(v.retention_seconds);
const same = (a, b) => ['owner_scope', 'generation', 'retention_seconds'].every(k => a[k] === b[k]);
const timeBefore = (v, end) => v === null || (date(v) && Date.parse(v) <= Date.parse(end));

export function validateSavedView(v) {
  if (!exact(v, ['kind', 'version', 'owner_scope', 'generation', 'field_set', 'retention_seconds', 'captured_at', 'currency', 'accounts', 'sources'])
      || v.kind !== 'saved-view' || v.version !== 1 || v.field_set !== 'generic-balances-v1' || !binding(v)
      || !date(v.captured_at) || v.currency !== 'USD' || !Array.isArray(v.accounts) || v.accounts.length < 1 || v.accounts.length > 20
      || !Array.isArray(v.sources) || v.sources.length > 10) fail('invalid_snapshot');
  const refs = new Set(), sourceRefs = new Set();
  for (const source of v.sources) {
    if (!exact(source, ['ref', 'provider', 'connection_state', 'last_attempt_at', 'attempt_outcome', 'last_successful_check_at', 'provider_as_of'])
        || !uuid(source.ref) || sourceRefs.has(source.ref) || !['teller', 'plaid', 'etrade'].includes(source.provider)
        || !['active', 'needs_reauth', 'disconnected', 'unavailable'].includes(source.connection_state)
        || !['unknown', 'running', 'success', 'partial', 'error'].includes(source.attempt_outcome)
        || !timeBefore(source.last_attempt_at, v.captured_at) || !timeBefore(source.last_successful_check_at, v.captured_at)
        || !timeBefore(source.provider_as_of, source.last_successful_check_at)) fail('invalid_snapshot');
    sourceRefs.add(source.ref);
  }
  for (const account of v.accounts) {
    if (!exact(account, ['ref', 'label', 'type', 'balance_minor', 'source_ref', 'observed_at', 'provider_as_of', 'freshness'])
        || !uuid(account.ref) || refs.has(account.ref) || !Object.hasOwn(LABELS, account.type)
        || !new RegExp(`^${LABELS[account.type]} ([1-9]|1[0-9]|20)$`).test(account.label)
        || !(account.balance_minor === null || (Number.isSafeInteger(account.balance_minor) && Math.abs(account.balance_minor) <= MAX_MINOR))
        || !(account.source_ref === null || sourceRefs.has(account.source_ref))
        || !['recorded', 'unrecorded', 'unverified'].includes(account.freshness)
        || !timeBefore(account.observed_at, v.captured_at) || !timeBefore(account.provider_as_of, account.observed_at)
        || (account.freshness === 'recorded' ? !account.observed_at || !account.source_ref : account.observed_at !== null || account.provider_as_of !== null)) fail('invalid_snapshot');
    refs.add(account.ref);
  }
  return v;
}

function validateAccess(v) {
  return exact(v, ['kind', 'version', 'active', 'owner_scope', 'generation', 'field_set', 'retention_seconds'])
    && v.kind === 'saved-view-access' && v.version === 1 && v.active === true && v.field_set === 'generic-balances-v1' && binding(v);
}
function base64(v) { return btoa(Array.from(v, x => String.fromCharCode(x)).join('')); }
function bytes(v, length) {
  if (typeof v !== 'string' || v.length > 180000 || !/^[A-Za-z0-9+/]*={0,2}$/.test(v)) fail('invalid_record');
  let result;
  try { result = Uint8Array.from(atob(v), x => x.charCodeAt(0)); } catch { fail('invalid_record'); }
  if (length && result.length !== length) fail('invalid_record');
  return result;
}
function aad(r) {
  return encoder.encode(JSON.stringify([r.kind, r.version, r.owner_scope, r.generation, r.retention_seconds, r.saved_at, r.expires_at, r.iterations]));
}
function metadata(r, now) {
  if (!exact(r, ['kind', 'version', 'owner_scope', 'generation', 'retention_seconds', 'saved_at', 'expires_at', 'iterations', 'salt', 'iv', 'ciphertext'])
      || r.kind !== 'encrypted-saved-view' || r.version !== 1 || !binding(r) || r.iterations !== ITERATIONS
      || !date(r.saved_at) || !date(r.expires_at) || Date.parse(r.saved_at) > now + 300000
      || Date.parse(r.expires_at) - Date.parse(r.saved_at) !== r.retention_seconds * 1000) fail('invalid_record');
  if (Date.parse(r.expires_at) <= now) fail('expired');
  bytes(r.salt, 16); bytes(r.iv, 12); bytes(r.ciphertext);
  return r;
}
async function key(phrase, salt, crypto) {
  if (typeof phrase !== 'string' || phrase.length < 12 || phrase.length > 256) fail('phrase_length');
  if (!crypto?.subtle) fail('crypto_unavailable');
  const raw = await crypto.subtle.importKey('raw', encoder.encode(phrase), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({name: 'PBKDF2', hash: 'SHA-256', salt, iterations: ITERATIONS}, raw,
    {name: 'AES-GCM', length: 256}, false, ['encrypt', 'decrypt']);
}

export async function sealSavedView(snapshot, phrase, {now = Date.now(), crypto = globalThis.crypto} = {}) {
  snapshot = structuredClone(snapshot); // Keep the validated payload stable during asynchronous key derivation.
  validateSavedView(snapshot);
  if (Date.parse(snapshot.captured_at) > now + 300000) fail('invalid_snapshot');
  if (!crypto?.subtle) fail('crypto_unavailable');
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
  const record = {kind: 'encrypted-saved-view', version: 1, owner_scope: snapshot.owner_scope, generation: snapshot.generation,
    retention_seconds: snapshot.retention_seconds, saved_at: new Date(now).toISOString(),
    expires_at: new Date(now + snapshot.retention_seconds * 1000).toISOString(), iterations: ITERATIONS,
    salt: base64(salt), iv: base64(iv), ciphertext: ''};
  const derived = await key(phrase, salt, crypto);
  record.ciphertext = base64(new Uint8Array(await crypto.subtle.encrypt({name: 'AES-GCM', iv, additionalData: aad(record)},
    derived, encoder.encode(JSON.stringify(snapshot)))));
  return record;
}

export async function openSavedView(record, phrase, {now = Date.now(), crypto = globalThis.crypto} = {}) {
  metadata(record, now);
  const derived = await key(phrase, bytes(record.salt, 16), crypto);
  let snapshot;
  try {
    snapshot = JSON.parse(decoder.decode(await crypto.subtle.decrypt({name: 'AES-GCM', iv: bytes(record.iv, 12), additionalData: aad(record)},
      derived, bytes(record.ciphertext))));
  } catch { fail('unlock_failed'); }
  validateSavedView(snapshot);
  if (!same(snapshot, record) || Date.parse(snapshot.captured_at) > Date.parse(record.saved_at) + 300000) fail('invalid_snapshot');
  return snapshot;
}

export class SavedViewClient {
  constructor(store, {enabled = false, fetcher = globalThis.fetch, now = Date.now, crypto = globalThis.crypto, onLock = () => {}} = {}) {
    this.store = store; this.enabled = enabled === true; this.fetcher = fetcher.bind(globalThis); this.now = now; this.crypto = crypto;
    this.onLock = onLock; this.revision = 0; this.snapshot = this.record = null; this.lastActivity = now(); this.connection = 'unknown'; this.denied = false;
  }
  allowed() { if (!this.enabled) fail('disabled'); }
  lock() { this.revision++; this.snapshot = this.record = null; this.onLock(); }
  async forget() { this.allowed(); this.lock(); await this.store.clear(); }
  async status() {
    this.allowed();
    const {record} = await this.store.read();
    if (!record) return 'empty';
    try { metadata(record, this.now()); return 'locked'; }
    catch (error) { await this.forget(); return error.code === 'expired' ? 'expired' : 'empty'; }
  }
  current(revision) { if (revision !== this.revision) fail('cancelled'); }
  async response(path) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 2500);
    try {
      return await this.fetcher(path, {cache: 'no-store', credentials: 'same-origin', redirect: 'manual', signal: controller.signal});
    } catch { return null; } finally { clearTimeout(timer); }
  }
  async access(expected = null) {
    this.allowed();
    const response = await this.response('/saved-view/access');
    if (!response || response.status >= 500) {
      this.connection = 'unavailable'; if (this.denied) fail('access_denied'); return null;
    }
    let grant;
    try { grant = await response.json(); } catch { /* Fail closed on a reachable malformed server. */ }
    if (!response.ok || response.redirected || response.type === 'opaqueredirect' || !validateAccess(grant) || (expected && !same(expected, grant))) {
      this.connection = 'denied'; this.denied = true; await this.forget(); fail('access_denied');
    }
    if (this.denied) { await this.forget(); this.denied = false; }
    this.connection = 'allowed';
    return grant;
  }
  async save(phrase) {
    this.allowed(); this.lock(); const revision = this.revision;
    const grant = await this.access(); this.current(revision);
    if (!grant) fail('online_required');
    const loaded = await this.store.read(); this.current(revision);
    const response = await this.response('/saved-view/snapshot'); this.current(revision);
    if (!response || response.status >= 500) fail('online_required');
    if (!response.ok || response.redirected || response.type === 'opaqueredirect') { await this.forget(); fail('access_denied'); }
    let snapshot;
    try { snapshot = validateSavedView(await response.json()); } catch { fail('invalid_snapshot'); }
    if (!same(snapshot, grant)) { await this.forget(); fail('access_denied'); }
    // Close policy changes during the export request; disconnected saving is denied.
    if (!await this.access(grant)) fail('online_required'); this.current(revision);
    const record = await sealSavedView(snapshot, phrase, {now: this.now(), crypto: this.crypto}); this.current(revision);
    if (!await this.store.write(record, loaded.revision)) fail('cancelled');
    if (revision !== this.revision) { await this.store.clear(); fail('cancelled'); }
    return record.saved_at;
  }
  async unlock(phrase) {
    this.allowed(); this.lock(); const revision = this.revision;
    const loaded = await this.store.read(); this.current(revision);
    if (!loaded.record) fail('empty');
    try { metadata(loaded.record, this.now()); } catch (error) { await this.forget(); throw error; }
    await this.access(loaded.record); this.current(revision); // Network loss permits a valid existing copy.
    let snapshot;
    try { snapshot = await openSavedView(loaded.record, phrase, {now: this.now(), crypto: this.crypto}); }
    catch (error) { if (['expired', 'invalid_record', 'invalid_snapshot'].includes(error.code)) await this.forget(); throw error; }
    const current = await this.store.read(); this.current(revision);
    if (loaded.revision !== current.revision || loaded.record.ciphertext !== current.record?.ciphertext) fail('cancelled');
    this.snapshot = snapshot; this.record = loaded.record; this.lastActivity = this.now();
    return snapshot;
  }
  async tick() {
    this.allowed();
    if (!this.snapshot) return;
    if (this.now() - this.lastActivity >= 300000) { this.lock(); fail('idle'); }
    let current;
    try { current = await this.store.read(); } catch (error) { this.lock(); throw error; }
    if (current.record?.ciphertext !== this.record?.ciphertext) { this.lock(); fail('cancelled'); }
    try { metadata(current.record, this.now()); } catch (error) { await this.forget(); throw error; }
  }
}
