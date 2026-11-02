import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {SavedViewClient, openSavedView, sealSavedView, validateSavedView} from '../mypocket/saved_view/client.mjs';

const NOW = Date.parse('2026-01-01T00:00:00Z'), PHRASE = 'fictional example only phrase';
const snapshot = () => ({kind: 'saved-view', version: 1, owner_scope: '11111111-1111-4111-8111-111111111111', generation: 1,
  field_set: 'generic-balances-v1', retention_seconds: 86400, captured_at: new Date(NOW).toISOString(), currency: 'USD',
  accounts: [{ref: '22222222-2222-4222-8222-222222222222', label: 'Checking 1', type: 'checking', balance_minor: 12345,
    source_ref: null, observed_at: null, provider_as_of: null, freshness: 'unrecorded'}], sources: []});
const grant = value => ({kind: 'saved-view-access', version: 1, active: true, owner_scope: value.owner_scope,
  generation: value.generation, field_set: value.field_set, retention_seconds: value.retention_seconds});
const response = (body, status = 200) => ({ok: status >= 200 && status < 300, status, redirected: false, json: async () => body});
const options = {now: NOW, crypto: webcrypto};
const clone = structuredClone;
const error = code => e => e.code === code;

class MemoryStore {
  record = null; revision = 0;
  async read() { return {record: clone(this.record), revision: this.revision}; }
  async write(record, revision) { if (revision !== this.revision) return false; this.record = clone(record); return true; }
  async clear() { this.record = null; this.revision++; }
}
function setup(value = snapshot()) {
  let now = NOW, mode = 'online', access = grant(value), status = 200;
  const store = new MemoryStore(), calls = [];
  const fetcher = async (path, init) => {
    assert.equal(this, undefined);
    calls.push({path, init});
    if (mode === 'offline') throw new Error('synthetic network unavailable');
    if (mode === 'unavailable') return response({}, 503);
    if (path.endsWith('/access')) return response(access, status);
    return response(value);
  };
  const client = new SavedViewClient(store, {enabled: true, fetcher, now: () => now, crypto: webcrypto});
  return {client, store, calls, setNow: v => { now = v; }, setMode: v => { mode = v; },
    setAccess: (v, code = 200) => { access = v; status = code; }};
}

test('personal payload is strictly allowlisted and unknown timestamps survive', () => {
  assert.deepEqual(validateSavedView(snapshot()), snapshot());
  for (const mutate of [v => { v.token = 'forbidden'; }, v => { v.accounts[0].name = 'forbidden'; },
    v => { v.accounts[0].label = 'Private name'; }, v => { v.accounts[0].balance_minor = NaN; },
    v => { v.currency = 'EUR'; }, v => { v.accounts.push(v.accounts[0]); }, v => { v.owner_scope = 'fictional-owner-v1'; },
    v => { v.accounts[0].freshness = 'recorded'; }, v => { v.accounts[0].provider_as_of = v.captured_at; }]) {
    const value = snapshot(); mutate(value); assert.throws(() => validateSavedView(value), error('invalid_snapshot'));
  }
});

test('disabled client has no storage or network side effects', async () => {
  const forbidden = () => { throw new Error('unexpected access'); };
  const client = new SavedViewClient(new Proxy({}, {get: forbidden}), {fetcher: forbidden});
  for (const operation of [() => client.save(PHRASE), () => client.unlock(PHRASE), () => client.forget(), () => client.access(), () => client.tick()]) {
    await assert.rejects(operation, error('disabled'));
  }
});

test('AES-GCM roundtrip stores no labels, amounts or phrase and randomizes ciphertext', async () => {
  const one = await sealSavedView(snapshot(), PHRASE, options), two = await sealSavedView(snapshot(), PHRASE, options);
  assert.notEqual(one.ciphertext, two.ciphertext); assert.notEqual(one.salt, two.salt); assert.notEqual(one.iv, two.iv);
  assert.deepEqual(await openSavedView(one, PHRASE, options), snapshot());
  const encoded = JSON.stringify(one);
  for (const text of ['Checking', 'balance_minor', PHRASE, '12345']) assert.equal(encoded.includes(text), false);
  await assert.rejects(() => openSavedView(one, 'different long phrase', options), error('unlock_failed'));
});

test('expiry, generation and owner metadata are authenticated', async () => {
  const record = await sealSavedView(snapshot(), PHRASE, options);
  for (const mutate of [r => { r.owner_scope = '33333333-3333-4333-8333-333333333333'; }, r => { r.generation++; },
    r => { r.retention_seconds *= 2; r.expires_at = new Date(NOW + r.retention_seconds * 1000).toISOString(); }]) {
    const changed = clone(record); mutate(changed);
    await assert.rejects(() => openSavedView(changed, PHRASE, options), error('unlock_failed'));
  }
  await assert.rejects(() => openSavedView(record, PHRASE, {...options, now: NOW + 86400000}), error('expired'));
});

test('future captures, short phrases and invalid retention are rejected', async () => {
  await assert.rejects(() => sealSavedView(snapshot(), 'short', options), error('phrase_length'));
  const future = snapshot(); future.captured_at = new Date(NOW + 3600000).toISOString();
  await assert.rejects(() => sealSavedView(future, PHRASE, options), error('invalid_snapshot'));
  const invalid = snapshot(); invalid.retention_seconds = 999999;
  await assert.rejects(() => sealSavedView(invalid, PHRASE, options), error('invalid_snapshot'));
});

test('save, relock and offline unlock use credentials only on same-origin no-store calls', async () => {
  const t = setup(); await t.client.save(PHRASE);
  assert.equal(t.client.snapshot, null);
  assert.equal((await t.client.unlock(PHRASE)).accounts[0].balance_minor, 12345);
  t.client.lock(); assert.equal(t.client.snapshot, null);
  t.setMode('offline'); await t.client.unlock(PHRASE); assert.equal(t.client.connection, 'unavailable');
  assert.equal(t.client.snapshot.captured_at, snapshot().captured_at);
  for (const call of t.calls) {
    assert.ok(call.path.startsWith('/saved-view/'));
    assert.equal(call.init.credentials, 'same-origin'); assert.equal(call.init.cache, 'no-store'); assert.equal(call.init.redirect, 'manual');
  }
});

test('wrong phrase preserves encrypted copy and leaves locked', async () => {
  const t = setup(); await t.client.save(PHRASE); t.setMode('offline');
  await assert.rejects(() => t.client.unlock('incorrect phrase long enough'), error('unlock_failed'));
  assert.ok(t.store.record); assert.equal(t.client.snapshot, null);
});

test('known denial and changed owner/generation remove the existing copy', async () => {
  for (const update of [() => ({body: {}, status: 401}), () => ({body: {...grant(snapshot()), active: false}, status: 200}),
    () => ({body: {...grant(snapshot()), generation: 2}, status: 200}),
    () => ({body: {...grant(snapshot()), owner_scope: '33333333-3333-4333-8333-333333333333'}, status: 200})]) {
    const t = setup(); await t.client.save(PHRASE);
    const {body, status} = update(); t.setAccess(body, status);
    await assert.rejects(() => t.client.unlock(PHRASE), error('access_denied'));
    assert.equal(t.store.record, null); assert.equal(t.client.snapshot, null);
    t.setMode('offline'); await assert.rejects(() => t.client.unlock(PHRASE), error('empty'));
  }
});

test('reachable malformed access fails closed; server failure permits an existing copy', async () => {
  const t = setup(); await t.client.save(PHRASE); t.setMode('unavailable');
  await t.client.unlock(PHRASE); assert.equal(t.client.connection, 'unavailable');
  t.client.lock(); t.setMode('online'); t.setAccess({kind: 'not-access'});
  await assert.rejects(() => t.client.unlock(PHRASE), error('access_denied'));
  assert.equal(t.store.record, null);
});

test('network loss never creates a snapshot or refreshes capture time', async () => {
  const t = setup(); t.setMode('offline');
  await assert.rejects(() => t.client.save(PHRASE), error('online_required'));
  assert.equal(t.store.record, null);
  await assert.rejects(() => t.client.unlock(PHRASE), error('empty'));
});

test('expiry removes ciphertext and idle relocks plaintext', async () => {
  const t = setup(); await t.client.save(PHRASE); t.setMode('offline'); await t.client.unlock(PHRASE);
  t.setNow(NOW + 300000); await assert.rejects(() => t.client.tick(), error('idle'));
  assert.equal(t.client.snapshot, null); assert.ok(t.store.record);
  t.setNow(NOW + 86400000); await assert.rejects(() => t.client.unlock(PHRASE), error('expired'));
  assert.equal(t.store.record, null);
});

test('removal in another window is detected before continuing to display', async () => {
  const t = setup(); await t.client.save(PHRASE); await t.client.unlock(PHRASE);
  await t.store.clear(); await assert.rejects(() => t.client.tick(), error('cancelled'));
  assert.equal(t.client.snapshot, null);
});

test('forget during encryption cannot recreate a deleted snapshot', async () => {
  const t = setup(); let begin, release;
  const started = new Promise(resolve => { begin = resolve; });
  const blocked = new Promise(resolve => { release = resolve; });
  t.client.crypto = {getRandomValues: webcrypto.getRandomValues.bind(webcrypto), subtle: {
    importKey: webcrypto.subtle.importKey.bind(webcrypto.subtle), deriveKey: webcrypto.subtle.deriveKey.bind(webcrypto.subtle),
    encrypt: async (...args) => { begin(); await blocked; return webcrypto.subtle.encrypt(...args); },
  }};
  const saving = t.client.save(PHRASE); await started;
  await t.client.forget(); release(); await assert.rejects(() => saving, error('cancelled'));
  assert.equal(t.store.record, null);
});

test('lock during decryption never displays late plaintext', async () => {
  const t = setup(); await t.client.save(PHRASE); let begin, release;
  const started = new Promise(resolve => { begin = resolve; }), blocked = new Promise(resolve => { release = resolve; });
  t.client.crypto = {subtle: {
    importKey: webcrypto.subtle.importKey.bind(webcrypto.subtle), deriveKey: webcrypto.subtle.deriveKey.bind(webcrypto.subtle),
    decrypt: async (...args) => { begin(); await blocked; return webcrypto.subtle.decrypt(...args); },
  }};
  const unlocking = t.client.unlock(PHRASE); await started; t.client.lock(); release();
  await assert.rejects(() => unlocking, error('cancelled')); assert.equal(t.client.snapshot, null);
});

test('native fetch retains its global receiver', async () => {
  const store = new MemoryStore();
  const client = new SavedViewClient(store, {enabled: true, now: () => NOW, crypto: webcrypto,
    fetcher: async function(path) { assert.equal(this, globalThis); return response(path.endsWith('/access') ? grant(snapshot()) : snapshot()); }});
  await client.save(PHRASE); assert.ok(store.record);
});

test('failed clear after observed revocation cannot unlock when connection disappears', async () => {
  const t = setup(); await t.client.save(PHRASE); await t.client.unlock(PHRASE);
  t.store.clear = async () => { throw new Error('synthetic storage failure'); };
  t.setAccess({}, 403);
  await assert.rejects(() => t.client.unlock(PHRASE), /synthetic storage failure/);
  assert.equal(t.client.snapshot, null); assert.ok(t.store.record);
  t.setMode('offline'); await assert.rejects(() => t.client.unlock(PHRASE), error('access_denied'));
});

test('storage read failure locks an already visible copy', async () => {
  const t = setup(); await t.client.save(PHRASE); await t.client.unlock(PHRASE);
  t.store.read = async () => { throw new Error('synthetic storage failure'); };
  await assert.rejects(() => t.client.tick(), /synthetic storage failure/);
  assert.equal(t.client.snapshot, null);
});

test('payload cannot change after validation while encryption is pending', async () => {
  const value = snapshot(), sealing = sealSavedView(value, PHRASE, options);
  value.accounts[0].balance_minor = 99999; value.account_number = 'forbidden';
  const decrypted = await openSavedView(await sealing, PHRASE, options);
  assert.deepEqual(decrypted, snapshot());
});
