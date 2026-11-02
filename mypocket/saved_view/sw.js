// Exact public shell only. API/auth paths and financial payloads are never cached.
const CACHE = 'mypocket-personal-shell-v1', PREFIX = 'mypocket-personal-shell-';
const SHELL = ['/saved-view/', '/saved-view/app.mjs', '/saved-view/client.mjs', '/saved-view/storage.mjs',
  '/saved-view/styles.css', '/saved-view/offline.css', '/saved-view/saved.css', '/saved-view/sky-daylight.jpg',
  '/saved-view/icon.svg', '/saved-view/manifest.webmanifest'];
async function prepareShell() {
  const cache = await caches.open(CACHE);
  for (const path of SHELL) {
    if (await cache.match(path)) continue;
    const response = await fetch(path, {cache: 'reload', credentials: 'omit', redirect: 'error'});
    if (!response.ok || response.redirected) throw new Error('shell_install_failed');
    await cache.put(path, response);
  }
}
self.addEventListener('install', event => event.waitUntil(prepareShell()));
self.addEventListener('message', event => {
  if (event.data?.type !== 'prepare-shell' || !event.ports[0]) return;
  event.waitUntil(prepareShell().then(() => event.ports[0].postMessage({ready: true}), () => event.ports[0].postMessage({ready: false})));
});
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const name of await caches.keys()) if (name.startsWith(PREFIX) && name !== CACHE) await caches.delete(name);
  await self.clients.claim();
})()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.search || !SHELL.includes(url.pathname)) return;
  event.respondWith((async () => (await (await caches.open(CACHE)).match(url.pathname)) || fetch(event.request, {credentials: 'omit', redirect: 'error'}))());
});
