import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../mypocket/offline_demo/sw.js', import.meta.url), 'utf8');
function runtime() {
  const listeners = {}, buckets = new Map(), calls = [], state = {online: true, claimed: false, redirect: false};
  const caches = {
    async open(name) {
      if (!buckets.has(name)) buckets.set(name, new Map());
      const bucket = buckets.get(name);
      return {put: async (path, response) => bucket.set(path, response.clone()), match: async path => bucket.get(path)?.clone()};
    },
    keys: async () => [...buckets.keys()], delete: async name => buckets.delete(name),
  };
  vm.runInNewContext(source, {URL, caches,
    self: {location: {origin: 'https://fictional.invalid'}, clients: {claim: async () => { state.claimed = true; }},
      addEventListener: (name, handler) => { listeners[name] = handler; }},
    fetch: async (path, init) => {
      calls.push({path, init}); if (!state.online) throw new Error('offline');
      if (state.redirect) return {ok: true, redirected: true};
      return new Response('shell:' + path);
    },
  });
  const lifecycle = name => new Promise((resolve, reject) => listeners[name]({waitUntil: promise => promise.then(resolve, reject)}));
  const request = (path, method = 'GET') => {
    let response;
    listeners.fetch({request: {url: new URL(path, 'https://fictional.invalid').href, method}, respondWith: promise => { response = promise; }});
    return response;
  };
  const prepare = () => new Promise((resolve, reject) => listeners.message({data: {type: 'prepare-shell'},
    ports: [{postMessage: resolve}], waitUntil: promise => promise.catch(reject)}));
  return {buckets, calls, state, lifecycle, request, prepare};
}

test('installation caches exactly a public shell with no cookies or provider/auth payloads', async () => {
  const worker = runtime(); await worker.lifecycle('install');
  const cached = [...worker.buckets.get('mypocket-fictional-shell-v1').keys()];
  assert.equal(cached.length, 9);
  assert.ok(cached.includes('/offline-demo/'));
  for (const path of ['/login', '/logout', '/api/sync', '/offline-demo/access', '/offline-demo/snapshot', '/']) assert.ok(!cached.includes(path));
  for (const call of worker.calls) { assert.equal(call.init.credentials, 'omit'); assert.equal(call.init.redirect, 'error'); }
});

test('cached shell reopens without the server; no broad navigation fallback', async () => {
  const worker = runtime(); await worker.lifecycle('install'); worker.state.online = false;
  const count = worker.calls.length;
  assert.equal(await (await worker.request('/offline-demo/')).text(), 'shell:/offline-demo/');
  assert.equal(await (await worker.request('/offline-demo/app.mjs')).text(), 'shell:/offline-demo/app.mjs');
  assert.equal(worker.calls.length, count);
  for (const path of ['/', '/login', '/api/accounts', '/offline-demo/access', '/offline-demo/snapshot', '/offline-demo/unknown', '/offline-demo/?owner=other']) {
    assert.equal(worker.request(path), undefined);
  }
  assert.equal(worker.request('/offline-demo/', 'POST'), undefined);
  assert.equal(worker.request('https://another.invalid/offline-demo/'), undefined);
});

test('activation only deletes this demo’s old shell caches', async () => {
  const worker = runtime();
  worker.buckets.set('mypocket-fictional-shell-v0', new Map());
  worker.buckets.set('unrelated-cache', new Map());
  await worker.lifecycle('install'); await worker.lifecycle('activate');
  assert.deepEqual([...worker.buckets.keys()].sort(), ['mypocket-fictional-shell-v1', 'unrelated-cache']);
  assert.equal(worker.state.claimed, true);
});

test('redirected pages cannot be installed into the shell cache', async () => {
  const worker = runtime(); worker.state.redirect = true;
  await assert.rejects(worker.lifecycle('install'), /shell_install_failed/);
  assert.equal(worker.buckets.get('mypocket-fictional-shell-v1').size, 0);
});

test('explicit save preparation restores evicted shell assets or reports failure', async () => {
  const worker = runtime(); await worker.lifecycle('install');
  worker.buckets.get('mypocket-fictional-shell-v1').delete('/offline-demo/app.mjs');
  worker.state.online = false;
  assert.equal((await worker.prepare()).ready, false);
  worker.state.online = true;
  assert.equal((await worker.prepare()).ready, true);
  worker.state.online = false;
  assert.equal(await (await worker.request('/offline-demo/app.mjs')).text(), 'shell:/offline-demo/app.mjs');
});
