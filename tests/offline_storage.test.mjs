import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import test from 'node:test';
import {checkAccess, ITERATIONS, openSnapshot, RETENTION_MS, sealSnapshot, SnapshotError, SnapshotVault, SCOPE, validateSnapshot} from '../mypocket/offline_demo/store.mjs';
import {OfflineSession} from '../mypocket/offline_demo/session.mjs';

const NOW = Date.parse('2026-11-01T12:00:00Z');
const PHRASE = 'fictional blue sky pocket';
const fixture = () => ({version: 1, fictional: true, owner_scope: SCOPE, captured_at: new Date(NOW).toISOString(), currency: 'USD',
  accounts: [{label: 'Fictional checking', type: 'checking', balance: 4800}, {label: 'Fictional card', type: 'credit', balance: 900}],
  sources: [{label: 'Demo bank', last_successful_check_at: '2026-11-01T11:30:00Z', provider_as_of: null}]});
const options = {now: NOW, crypto: webcrypto};
const rejects = (promise, code) => assert.rejects(promise, error => error.code === code);
class MemoryStore {
  constructor() { this.record = null; this.revision = 0; }
  async read() { return {record: structuredClone(this.record), revision: this.revision}; }
  async write(record, revision) {
    if (revision !== this.revision) return false;
    this.record = structuredClone(record); return true;
  }
  async clear() { this.record = null; this.revision++; }
}
function context() {
  const store = new MemoryStore(), clock = {value: NOW};
  return {store, clock, vault: new SnapshotVault(store, {now: () => clock.value, crypto: webcrypto})};
}
function gateCrypto() {
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  const subtle = new Proxy(webcrypto.subtle, {get(target, key) {
    if (key === 'deriveKey') return async (...args) => { entered(); await gate; return target.deriveKey(...args); };
    return typeof target[key] === 'function' ? target[key].bind(target) : target[key];
  }});
  return {crypto: {subtle, getRandomValues: webcrypto.getRandomValues.bind(webcrypto)}, started, release};
}
const allowed = () => Response.json({fictional: true, active: true, owner_scope: SCOPE});

test('round trip encrypts all values and source timestamps with an unpersisted phrase', async () => {
  const record = await sealSnapshot(fixture(), PHRASE, options);
  const serialized = JSON.stringify(record);
  for (const forbidden of [PHRASE, '4800', 'Fictional checking', 'Demo bank', 'last_successful_check_at', '2026-11-01T11:30:00Z']) {
    assert.equal(serialized.includes(forbidden), false);
  }
  assert.equal(record.iterations, ITERATIONS);
  assert.deepEqual(Object.keys(record).sort(), ['version', 'scope', 'saved_at', 'expires_at', 'iterations', 'salt', 'iv', 'ciphertext'].sort());
  assert.deepEqual(await openSnapshot(record, PHRASE, options), fixture());
  const another = await sealSnapshot(fixture(), PHRASE, options);
  assert.notEqual(record.salt, another.salt); assert.notEqual(record.iv, another.iv); assert.notEqual(record.ciphertext, another.ciphertext);
});

test('wrong phrase and changed ciphertext cannot unlock', async () => {
  const record = await sealSnapshot(fixture(), PHRASE, options);
  await rejects(openSnapshot(record, 'another fictional phrase', options), 'unlock_failed');
  record.ciphertext = (record.ciphertext.startsWith('A') ? 'B' : 'A') + record.ciphertext.slice(1);
  await rejects(openSnapshot(record, PHRASE, options), 'unlock_failed');
});

test('timestamps are authenticated so changing expiry and saved time cannot extend access', async () => {
  const record = await sealSnapshot(fixture(), PHRASE, options);
  record.saved_at = new Date(NOW + 60000).toISOString();
  record.expires_at = new Date(NOW + RETENTION_MS + 60000).toISOString();
  await rejects(openSnapshot(record, PHRASE, {...options, now: NOW + 60000}), 'unlock_failed');
});

test('strict schema rejects real data markers, extra tokens, personal payloads and invalid values', () => {
  for (const mutation of [
    value => { value.fictional = false; },
    value => { value.access_token = 'synthetic-extra'; },
    value => { value.accounts[0].account_number = 'fictional-extra'; },
    value => { value.sources[0].provider_payload = {}; },
    value => { value.accounts[0].balance = Infinity; },
    value => { value.sources[0].provider_as_of = 'yesterday'; },
    value => { value.owner_scope = 'another-owner'; },
  ]) {
    const value = fixture(); mutation(value);
    assert.throws(() => validateSnapshot(value), error => error.code === 'invalid_snapshot');
  }
  assert.equal(validateSnapshot(fixture()).sources[0].provider_as_of, null);
});

test('expiry and owner mismatch clear stored data before it can be shown', async () => {
  const {store, vault, clock} = context();
  await vault.save(fixture(), PHRASE);
  clock.value = NOW + RETENTION_MS;
  await rejects(vault.unlock(PHRASE), 'expired');
  assert.equal(store.record, null); assert.equal(vault.snapshot, null);
  clock.value = NOW; await vault.save(fixture(), PHRASE);
  const other = new SnapshotVault(store, {scope: 'another-owner', now: () => NOW, crypto: webcrypto});
  assert.equal(await other.status(), 'empty'); assert.equal(store.record, null);
});

test('lock removes plaintext; forget removes ciphertext; evicted storage fails empty', async () => {
  const {store, vault} = context();
  await vault.save(fixture(), PHRASE); await vault.unlock(PHRASE);
  assert.ok(vault.snapshot); vault.lock();
  assert.equal(vault.snapshot, null); assert.equal(vault.record, null); assert.ok(store.record);
  await vault.forget(); assert.equal(store.record, null);
  await rejects(vault.unlock(PHRASE), 'empty');
});

test('forget during pending encryption cannot resurrect a cleared snapshot', async () => {
  const {store, vault} = context(); const gate = gateCrypto(); vault.crypto = gate.crypto;
  const saving = vault.save(fixture(), PHRASE); await gate.started;
  await vault.forget(); gate.release();
  await rejects(saving, 'cancelled'); assert.equal(store.record, null);
});

test('clear from another window during encryption invalidates the pending storage write', async () => {
  const {store, vault} = context(); const gate = gateCrypto(); vault.crypto = gate.crypto;
  const saving = vault.save(fixture(), PHRASE); await gate.started;
  await store.clear(); gate.release();
  await rejects(saving, 'cancelled'); assert.equal(store.record, null);
});

test('lock during pending unlock cannot expose late plaintext', async () => {
  const {vault} = context(); await vault.save(fixture(), PHRASE);
  const gate = gateCrypto(); vault.crypto = gate.crypto;
  const unlocking = vault.unlock(PHRASE); await gate.started;
  vault.lock(); gate.release();
  await rejects(unlocking, 'cancelled'); assert.equal(vault.snapshot, null);
});

test('another window clearing or replacing data locks an already unlocked view', async () => {
  const {vault, store} = context(); await vault.save(fixture(), PHRASE); await vault.unlock(PHRASE);
  await store.clear(); await rejects(vault.assertCurrent(), 'cancelled'); assert.equal(vault.snapshot, null);
});

test('known denied access and owner changes fail closed; server unavailable remains explicit', async () => {
  assert.equal(await checkAccess(async () => allowed()), 'allowed');
  for (const response of [new Response('', {status: 401}), new Response('', {status: 403}), new Response('', {status: 302}),
    Response.json(null), Response.json({}),
    Response.json({fictional: true, active: false, owner_scope: SCOPE}),
    Response.json({fictional: true, active: true, owner_scope: 'another-owner'}),
    Response.json({active: true, owner_scope: SCOPE}), new Response('not-json')]) {
    assert.equal(await checkAccess(async () => response), 'denied');
  }
  assert.equal(await checkAccess(async () => { throw new Error('offline'); }), 'unavailable');
  assert.equal(await checkAccess(async () => new Response('', {status: 503})), 'unavailable');
});

test('offline session unlocks only a previously saved valid snapshot, without live fetches', async () => {
  const {vault} = context(), calls = [];
  const session = new OfflineSession(vault, {now: () => NOW, fetcher: async (path, init) => {
    calls.push(path); assert.equal(init.credentials, 'omit'); assert.equal(init.cache, 'no-store'); throw new Error('offline');
  }});
  await rejects(session.unlock(PHRASE), 'empty');
  await vault.save(fixture(), PHRASE);
  assert.deepEqual(await session.unlock(PHRASE), fixture());
  assert.equal(session.connection, 'unavailable');
  assert.deepEqual(calls, ['/offline-demo/access', '/offline-demo/access']);
  await rejects(session.save(PHRASE), 'online_required');
});

test('observed revocation removes ciphertext and cannot expose a valid saved snapshot', async () => {
  const {store, vault} = context(); await vault.save(fixture(), PHRASE);
  const session = new OfflineSession(vault, {fetcher: async () => new Response('', {status: 403})});
  await rejects(session.unlock(PHRASE), 'access_denied');
  assert.equal(store.record, null); assert.equal(vault.snapshot, null);
});

test('observed revocation blanks the view before a slow storage clear finishes', async () => {
  const {store, vault} = context(); await vault.save(fixture(), PHRASE); await vault.unlock(PHRASE);
  let cleared, began, locked = false;
  const started = new Promise(resolve => { began = resolve; });
  store.clear = () => { began(); return new Promise(resolve => { cleared = resolve; }); };
  const session = new OfflineSession(vault, {onLock: () => { locked = true; }, fetcher: async () => new Response('', {status: 403})});
  const checking = session.access(); await started;
  assert.equal(locked, true); assert.equal(vault.snapshot, null);
  cleared(); await rejects(checking, 'access_denied');
});

test('a failed revocation clear stays locked if the connection then disappears', async () => {
  const {store, vault} = context(); await vault.save(fixture(), PHRASE);
  store.clear = async () => { throw new SnapshotError('storage_unavailable'); };
  const session = new OfflineSession(vault, {fetcher: async () => new Response('', {status: 403})});
  await rejects(session.unlock(PHRASE), 'storage_unavailable');
  session.fetcher = async () => { throw new Error('offline'); };
  await rejects(session.unlock(PHRASE), 'access_denied');
  assert.equal(vault.snapshot, null);
});

test('online save fetches only fictional uncached endpoints and does not unlock automatically', async () => {
  const {store, vault} = context(), calls = [];
  const session = new OfflineSession(vault, {fetcher: async (path, init) => {
    calls.push(path); assert.equal(init.credentials, 'omit'); assert.equal(init.cache, 'no-store');
    return path.endsWith('/access') ? allowed() : Response.json(fixture());
  }});
  await session.save(PHRASE);
  assert.deepEqual(calls, ['/offline-demo/access', '/offline-demo/snapshot']);
  assert.ok(store.record); assert.equal(vault.snapshot, null);
});

test('session transport preserves the Window/global receiver required by browser fetch', async () => {
  const {vault} = context();
  const session = new OfflineSession(vault, {fetcher: function(path) {
    assert.equal(this, globalThis);
    return Promise.resolve(path.endsWith('/access') ? allowed() : Response.json(fixture()));
  }});
  await session.save(PHRASE);
  assert.deepEqual(await session.unlock(PHRASE), fixture());
});

test('session hide/lock cancels a pending access check before decryption or saving', async () => {
  const {vault} = context(); await vault.save(fixture(), PHRASE);
  let resolve;
  const session = new OfflineSession(vault, {fetcher: () => new Promise(ready => { resolve = ready; })});
  const unlocking = session.unlock(PHRASE); session.lock(); resolve(allowed());
  await rejects(unlocking, 'cancelled'); assert.equal(vault.snapshot, null);
  const saving = session.save(PHRASE); session.lock(); resolve(allowed());
  await rejects(saving, 'cancelled');
});

test('idle timeout and retention expiry lock a currently open view', async () => {
  const {vault, clock, store} = context(); await vault.save(fixture(), PHRASE);
  const session = new OfflineSession(vault, {now: () => clock.value, fetcher: async () => allowed()});
  await session.unlock(PHRASE); clock.value += 5 * 60 * 1000;
  await rejects(session.tick(), 'idle'); assert.equal(vault.snapshot, null);
  await session.unlock(PHRASE); clock.value = NOW + RETENTION_MS; session.lastActivity = clock.value;
  await rejects(session.tick(), 'expired'); assert.equal(store.record, null); assert.equal(vault.snapshot, null);
});

test('unsupported crypto and short phrases fail without producing a record', async () => {
  await rejects(sealSnapshot(fixture(), PHRASE, {...options, crypto: null}), 'crypto_unavailable');
  await rejects(sealSnapshot(fixture(), 'short', options), 'phrase_length');
});
