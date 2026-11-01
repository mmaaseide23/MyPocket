// Fictional-only browser validation. Never connects to the live app or a provider.
// Uses a new temporary Chromium profile; browser sandbox remains enabled.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const ROOT = path.resolve(__dirname, '..');
const REVIEW = path.join(ROOT, 'review');
const PHRASE = 'fictional browser blue sky';
const report = {scope: 'Fictional-only isolated Chromium; no real account data or credentials', checks: [],
  browser_sandbox_enabled: true, live_app_accessed: false, physical_iphone_tested: false};
let browser, server, context, phase = 'launch';
const pass = name => { report.checks.push({name, status: 'passed'}); console.log(`PASS: ${name}`); };

async function launchFixture() {
  const child = spawn(process.env.MYPOCKET_TEST_PYTHON || 'python', ['-m', 'tests.run_offline_fixture'], {
    cwd: ROOT, env: {PATH: process.env.PATH, PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1'},
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server = child;
  return new Promise((resolve, reject) => {
    let pending = '';
    const timer = setTimeout(() => reject(new Error('Fictional fixture startup timed out')), 10000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Fictional fixture exited: ${code}`)); });
    child.stdout.on('data', bytes => {
      pending += bytes.toString();
      if (!pending.includes('\n')) return;
      clearTimeout(timer);
      try { resolve(`http://127.0.0.1:${JSON.parse(pending.split('\n')[0]).port}`); }
      catch { reject(new Error('Invalid fixture startup response')); }
    });
    child.stderr.on('data', () => {}); // No application/financial logs are read.
  });
}
async function state(page, text) {
  await page.waitForFunction(expected => document.getElementById('status').textContent.includes(expected), text);
}
async function hidden(page) {
  assert.equal(await page.locator('#snapshot').isVisible(), false);
  assert.equal(await page.locator('#net-worth').textContent(), '');
  assert.equal(await page.locator('#accounts li').count(), 0);
}
async function unlock(page, phrase = PHRASE) {
  await page.locator('#phrase').fill(phrase);
  await page.locator('#unlock').click();
  await page.waitForFunction(() => document.getElementById('state').textContent === 'Unlocked');
}
async function save(page) {
  await page.locator('#phrase').fill(PHRASE);
  await page.locator('#save').click();
  await state(page, 'Fictional snapshot saved');
}
async function stored(page) {
  return page.evaluate(async () => {
    const {IndexedSnapshotStore} = await import('/offline-demo/store.mjs');
    return new IndexedSnapshotStore().read();
  });
}

(async () => {
  await fs.mkdir(REVIEW, {recursive: true});
  // A fresh temporary profile keeps this fictional test separate from regular browsing.
  browser = await chromium.launch({headless: true, chromiumSandbox: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    env: {PATH: process.env.PATH}, timeout: 15000});
  report.browser_version = browser.version(); pass('Isolated Chromium launch with browser sandbox enabled');
  phase = 'fixture startup';
  const origin = await launchFixture();
  context = await browser.newContext({viewport: {width: 390, height: 844}, deviceScaleFactor: 1,
    isMobile: true, hasTouch: true, colorScheme: 'light', locale: 'en-US', serviceWorkers: 'allow'});
  const unexpected = [], pageErrors = [], requests = [];
  report.page_errors = pageErrors; report.console_errors = [];
  report.request_paths = requests;
  let revoked = false;
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) { unexpected.push(url.origin); return route.abort(); }
    if (revoked && url.pathname === '/offline-demo/access') {
      return route.fulfill({status: 403, contentType: 'application/json', body: JSON.stringify({fictional: true, active: false})});
    }
    return route.continue();
  });
  context.on('request', request => requests.push(new URL(request.url()).pathname));
  await context.addInitScript(() => {
    const realNow = Date.now.bind(Date); let offset = 0;
    Date.now = () => realNow() + offset;
    globalThis.__fictionalAdvanceClock = milliseconds => { offset += milliseconds; };
  });
  const page = await context.newPage(); page.setDefaultTimeout(30000);
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') report.console_errors.push(message.text()); });
  phase = 'empty state';
  // The loopback listener is bound before its ephemeral port is reported.
  await page.goto(origin + '/offline-demo/', {waitUntil: 'networkidle'});
  await state(page, 'No saved snapshot'); await hidden(page);
  assert.equal(await page.evaluate(() => navigator.serviceWorker.controller), null);
  pass('Empty snapshot shows no balances; page load does not register a worker');

  phase = 'save and encryption';
  await save(page); await hidden(page);
  const {record} = await stored(page), serialized = JSON.stringify(record);
  assert.deepEqual(Object.keys(record).sort(), ['version', 'scope', 'saved_at', 'expires_at', 'iterations', 'salt', 'iv', 'ciphertext'].sort());
  for (const text of [PHRASE, 'Everyday checking', 'Rainy-day savings', '"balance"', 'last_successful_check_at']) assert.ok(!serialized.includes(text));
  assert.equal(await page.locator('#phrase').inputValue(), '');
  assert.deepEqual(await page.evaluate(() => Object.keys(localStorage)), []);
  assert.deepEqual(await context.cookies(), []);
  pass('Actual IndexedDB stores encrypted data only; no phrase, localStorage values or cookies');

  phase = 'unlock and mobile rendering';
  await page.locator('#phrase').fill('incorrect fictional phrase'); await page.locator('#unlock').click();
  await state(page, 'Could not unlock'); await hidden(page);
  await unlock(page);
  assert.equal(await page.locator('#net-worth').textContent(), '$85,900');
  assert.equal(await page.locator('#accounts li').count(), 5);
  assert.equal(await page.getByText('Provider data as of: unknown · timestamp not supplied', {exact: true}).count(), 2);
  assert.match(await page.locator('#captured-at').textContent(), /Snapshot captured/);
  assert.match(await page.locator('#saved-at').textContent(), /Saved to this device/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({path: path.join(REVIEW, 'offline-phone.png'), fullPage: true});
  pass('Wrong phrase rejected; correct unlock shows dated fictional balances and unknown provider times; 390px layout fits');

  phase = 'lock and reload';
  await page.locator('#lock').click(); await hidden(page); assert.ok((await stored(page)).record);
  await unlock(page); await page.reload({waitUntil: 'networkidle'});
  await state(page, 'encrypted fictional snapshot'); await hidden(page);
  pass('Explicit lock and real page reload remove displayed plaintext while retaining ciphertext');

  phase = 'cache boundary';
  const cacheState = await page.evaluate(async () => {
    const registrations = await navigator.serviceWorker.getRegistrations();
    const names = await caches.keys(); const paths = [];
    for (const name of names) for (const request of await (await caches.open(name)).keys()) paths.push(new URL(request.url).pathname);
    return {scopes: registrations.map(item => new URL(item.scope).pathname), names, paths};
  });
  assert.deepEqual(cacheState.scopes, ['/offline-demo/']);
  assert.deepEqual(cacheState.names, ['mypocket-fictional-shell-v1']);
  assert.equal(cacheState.paths.length, 9);
  for (const forbidden of ['/offline-demo/snapshot', '/offline-demo/access', '/login', '/logout', '/api/accounts', '/']) assert.ok(!cacheState.paths.includes(forbidden));
  pass('Actual worker caches only nine shell assets within /offline-demo/; auth/API/snapshot excluded');

  phase = 'offline reload';
  await context.setOffline(true);
  await page.reload({waitUntil: 'domcontentloaded'});
  await state(page, 'encrypted fictional snapshot'); await hidden(page);
  await unlock(page);
  assert.match(await page.locator('#connection').textContent(), /Server unavailable/);
  assert.equal(await page.locator('#net-worth').textContent(), '$85,900');
  await page.screenshot({path: path.join(REVIEW, 'offline-phone-disconnected.png'), fullPage: true});
  const blocked = await page.evaluate(async () => {
    const result = {};
    for (const path of ['/login', '/api/accounts', '/offline-demo/snapshot', '/offline-demo/access', '/offline-demo/unknown']) {
      try { await fetch(path, {cache: 'no-store'}); result[path] = false; } catch { result[path] = true; }
    }
    return result;
  });
  assert.ok(Object.values(blocked).every(Boolean));
  pass('Network-off reload opens cached shell and decrypts saved data; unknown/API/auth requests fail offline');

  phase = 'idle and expiry';
  await page.evaluate(() => globalThis.__fictionalAdvanceClock(5 * 60 * 1000 + 1));
  await state(page, 'Locked after 5 minutes'); await hidden(page); assert.ok((await stored(page)).record);
  await page.evaluate(() => globalThis.__fictionalAdvanceClock(-(5 * 60 * 1000 + 1)));
  await unlock(page);
  await page.evaluate(() => { globalThis.__fictionalAdvanceClock(24 * 60 * 60 * 1000 + 1); document.dispatchEvent(new Event('pointerdown')); });
  await state(page, 'snapshot expired'); await hidden(page); assert.equal((await stored(page)).record, null);
  pass('Browser idle handling locks; expired snapshot is removed from actual IndexedDB');

  phase = 'remove and revocation';
  await context.setOffline(false); await page.reload({waitUntil: 'networkidle'}); await state(page, 'No saved snapshot');
  await save(page); await unlock(page);
  await page.locator('#forget').click(); await state(page, 'Saved data removed'); await hidden(page); assert.equal((await stored(page)).record, null);
  await context.setOffline(true); await page.reload({waitUntil: 'domcontentloaded'});
  await state(page, 'No saved snapshot'); await hidden(page);
  await page.locator('#phrase').fill(PHRASE); await page.locator('#unlock').click();
  await state(page, 'No saved snapshot'); await hidden(page);
  await context.setOffline(false); await page.reload({waitUntil: 'networkidle'});
  pass('Cached shell with no snapshot stays empty offline; unlocking does not invent sample balances');

  phase = 'unknown last-check timestamp';
  await page.evaluate(async phrase => {
    const {IndexedSnapshotStore, SnapshotVault} = await import('/offline-demo/store.mjs');
    const snapshot = await (await fetch('/offline-demo/snapshot', {cache: 'no-store', credentials: 'omit'})).json();
    snapshot.sources[0].last_successful_check_at = null;
    await new SnapshotVault(new IndexedSnapshotStore()).save(snapshot, phrase);
  }, PHRASE);
  await page.reload({waitUntil: 'networkidle'}); await unlock(page);
  assert.equal(await page.getByText('Last successful check: Unknown', {exact: true}).count(), 1);
  pass('Missing successful-check timestamp stays explicitly Unknown after encrypted save and reload');

  phase = 'revocation';
  await save(page); revoked = true;
  await page.locator('#phrase').fill(PHRASE); await page.locator('#unlock').click();
  await state(page, 'no longer has access'); await hidden(page); assert.equal((await stored(page)).record, null);
  revoked = false; await page.reload({waitUntil: 'networkidle'});
  pass('Manual Forget and observed revocation clear IndexedDB before a saved view can reopen');

  phase = 'cross-window clear and desktop';
  await save(page); await unlock(page);
  const second = await context.newPage(); second.setDefaultTimeout(12000);
  await second.goto(origin + '/offline-demo/', {waitUntil: 'networkidle'}); await state(second, 'encrypted fictional snapshot');
  await second.locator('#forget').click(); await state(second, 'Saved data removed');
  await state(page, 'removed in another window'); await hidden(page); assert.equal((await stored(page)).record, null);
  await second.close(); await page.bringToFront();
  await save(page); await unlock(page); await page.setViewportSize({width: 1280, height: 900});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({path: path.join(REVIEW, 'offline-desktop.png'), fullPage: true});
  pass('Cross-window removal blanks other views; 1280px layout fits');

  phase = 'cleanup';
  await page.locator('#forget').click(); await state(page, 'Saved data removed');
  await page.evaluate(async () => {
    for (const registration of await navigator.serviceWorker.getRegistrations()) await registration.unregister();
    for (const name of await caches.keys()) if (name.startsWith('mypocket-fictional-shell-')) await caches.delete(name);
  });
  assert.deepEqual(unexpected, []); assert.deepEqual(pageErrors, []);
  assert.ok(requests.every(item => item.startsWith('/offline-demo/') || ['/login', '/api/accounts'].includes(item)));
  report.status = 'passed'; report.external_requests = unexpected.length; report.page_errors = pageErrors;
  report.remaining = ['Physical iPhone installation/storage and Mac-closed acceptance test', 'Real-data policy approval and live export/logout integration', 'Offline devices cannot discover unseen remote revocation'];
  pass('No external requests or page errors; fictional record, worker and caches removed');
})().catch(async error => {
  report.status = phase === 'launch' ? 'browser_launch_blocked' : 'failed';
  report.failed_phase = phase; report.error = error.message;
  const page = context?.pages()[0];
  if (page && !page.isClosed()) {
    report.failure_diagnostics = await page.evaluate(async () => ({
      status: document.getElementById('status')?.textContent,
      state: document.getElementById('state')?.textContent,
      secure: isSecureContext,
      workers: (await navigator.serviceWorker.getRegistrations()).map(registration => ({scope: registration.scope,
        active: registration.active?.state, installing: registration.installing?.state, waiting: registration.waiting?.state})),
      caches: await caches.keys(),
    })).catch(() => ({unavailable: true}));
    await page.screenshot({path: path.join(REVIEW, 'offline-failure.png'), fullPage: true}).catch(() => {});
  }
  console.error(`${report.status}: ${phase}: ${error.message}`); process.exitCode = 1;
}).finally(async () => {
  if (context) await context.close().catch(() => {});
  if (browser) await browser.close().catch(() => {});
  if (server && server.exitCode === null) {
    server.kill('SIGTERM');
    await new Promise(resolve => { server.once('exit', resolve); setTimeout(resolve, 3000); });
    if (server.exitCode === null) server.kill('SIGKILL');
  }
  report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(REVIEW, 'offline-browser-validation.json'), JSON.stringify(report, null, 2) + '\n');
});
