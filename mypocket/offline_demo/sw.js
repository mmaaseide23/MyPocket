// This worker is scoped to an isolated fictional preview, never the live app.
const CACHE = 'mypocket-fictional-shell-v1';
const PREFIX = 'mypocket-fictional-shell-';
const SHELL = [
  '/offline-demo/', '/offline-demo/app.mjs', '/offline-demo/store.mjs',
  '/offline-demo/session.mjs', '/offline-demo/styles.css', '/offline-demo/offline.css',
  '/offline-demo/sky-daylight.jpg', '/offline-demo/icon.svg', '/offline-demo/manifest.webmanifest',
];

async function prepareShell() {
    const cache = await caches.open(CACHE);
    // Never cache redirects (including login redirects), credentials or API responses.
    for (const path of SHELL) {
      if (await cache.match(path)) continue;
      const response = await fetch(path, {cache: 'reload', credentials: 'omit', redirect: 'error'});
      if (!response.ok || response.redirected) throw new Error('shell_install_failed');
      await cache.put(path, response);
    }
}
self.addEventListener('install', event => {
  event.waitUntil(prepareShell());
});
self.addEventListener('message', event => {
  if (event.data?.type !== 'prepare-shell' || !event.ports[0]) return;
  event.waitUntil(prepareShell().then(
    () => event.ports[0].postMessage({ready: true}),
    () => event.ports[0].postMessage({ready: false}),
  ));
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith(PREFIX) && name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.search
      || !SHELL.includes(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE), cached = await cache.match(url.pathname);
    return cached || fetch(event.request, {credentials: 'omit', redirect: 'error'});
  })());
});
