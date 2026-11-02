// Full personal protocol with invented data only; new sandboxed Chromium profile.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const ROOT = path.resolve(__dirname, '..'), REVIEW = path.join(ROOT, 'review');
const PHRASE = 'fictional personal protocol phrase';
const report = {synthetic_only:true, browser_sandbox_enabled:true, physical_iphone_tested:false, checks:[]};
let browser, server, context, phase = 'launch';
const pass = name => { report.checks.push(name); console.log('PASS: ' + name); };
async function fixture() {
  server = spawn(process.env.MYPOCKET_TEST_PYTHON || 'python', ['-m','tests.run_saved_view_fixture'], {
    cwd:ROOT, env:{PATH:process.env.PATH,PYTHONDONTWRITEBYTECODE:'1',DATABASE_URL:'sqlite://',PYTHONUNBUFFERED:'1'}, stdio:['ignore','pipe','pipe']});
  return new Promise((resolve,reject) => {
    let output = ''; const timer = setTimeout(() => reject(new Error('Fixture startup timeout')),10000);
    server.once('error', error => { clearTimeout(timer); reject(error); });
    server.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exit ' + code)); });
    server.stdout.on('data', data => { output += data; if (output.includes('\n')) { clearTimeout(timer); resolve('http://127.0.0.1:' + JSON.parse(output.split('\n')[0]).port); } });
    server.stderr.on('data', () => {});
  });
}
async function state(page,text) { await page.waitForFunction(text => document.getElementById('status').textContent.includes(text),text); }
async function stored(page) { return page.evaluate(async () => new (await import('/saved-view/storage.mjs')).SavedViewStore().read()); }
async function save(page) { await page.locator('#phrase').fill(PHRASE); await page.locator('#save').click(); await state(page,'Last synced copy saved'); }
async function unlock(page,phrase=PHRASE) { await page.locator('#phrase').fill(phrase); await page.locator('#unlock').click(); await page.waitForFunction(() => document.getElementById('state').textContent === 'Unlocked'); }
async function approve(page,history=true) {
  await page.locator('#approval-panel').evaluate(node => { node.open = true; });
  await page.locator('input[name=account][value="1"]').check();
  await page.locator('input[name=account][value="3"]').check();
  await page.locator('#retention').selectOption('86400');
  for (const id of ['confirm-fields','confirm-offline','confirm-phrase']) await page.locator('#'+id).check();
  await page.locator('#history').setChecked(history);
  await page.locator('#approve').click(); await state(page,'Policy approved');
}
(async () => {
  await fs.mkdir(REVIEW,{recursive:true});
  browser = await chromium.launch({headless:true,chromiumSandbox:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,env:{PATH:process.env.PATH},timeout:15000});
  const origin = await fixture();
  context = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,locale:'en-US',serviceWorkers:'allow'});
  const unexpected = [], errors = [];
  await context.route('**/*', route => { if (new URL(route.request().url()).origin !== origin) { unexpected.push(route.request().url()); return route.abort(); } return route.continue(); });
  await context.addInitScript(() => { const original = Date.now; let offset=0; Date.now=()=>original()+offset; globalThis.__fictionalClock = value => { offset=value; }; });
  await context.request.post(origin+'/fixture/login');
  const page = await context.newPage(); page.setDefaultTimeout(30000); page.on('pageerror', e => errors.push(e.message));
  phase='empty policy';
  const response = await page.goto(origin+'/saved-view/',{waitUntil:'networkidle'});
  assert.ok(response.headers()['content-security-policy'].includes("default-src 'none'"));
  assert.ok(!response.headers()['content-security-policy'].includes('unsafe-'));
  await state(page,'No saved copy');
  await page.waitForSelector('input[name=account]',{state:'attached'});
  assert.equal(await page.locator('input:checked').count(),0); assert.equal(await page.locator('#retention').inputValue(),'');
  assert.equal((await stored(page)).record,null);
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length),0);
  pass('Strict CSP, empty storage, no preselected consent and no automatic worker');
  await page.locator('#phrase').fill(PHRASE); await page.locator('#save').click(); await state(page,'Access was denied');
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length),0);
  pass('Saving without explicit approval fails before caching a shell or snapshot');
  phase='approval'; await approve(page);
  assert.equal((await stored(page)).record,null);
  pass('Account/field/retention/history approval does not automatically save');
  await context.request.post(origin+'/fixture/check'); await context.request.post(origin+'/fixture/check');
  phase='save'; await save(page);
  const encrypted = (await stored(page)).record;
  assert.ok(encrypted.ciphertext); const encoded = JSON.stringify(encrypted);
  for (const forbidden of [PHRASE,'Checking','balance_minor','4800','Fictional checking']) assert.ok(!encoded.includes(forbidden));
  assert.equal(await page.locator('#phrase').inputValue(),'');
  assert.deepEqual(await page.evaluate(() => Object.keys(localStorage)),[]);
  pass('Actual personal IndexedDB stores ciphertext with no phrase or account fields');
  phase='unlock'; await unlock(page);
  await page.waitForFunction(() => document.getElementById('changes').textContent.includes('Cause not determined'));
  assert.ok((await page.locator('#changes').textContent()).includes('-$10.00 toward your selected total'));
  assert.equal(await page.locator('#accounts li').count(),2);
  assert.ok((await page.locator('#sources').textContent()).includes('Provider data as of: Unknown'));
  const captured = await page.locator('#captured-at').textContent();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path:path.join(REVIEW,'saved-view-mobile.png'),fullPage:true});
  pass('Mobile view shows selected balances, real check windows and unknown provider times');
  await page.locator('#lock').click();
  await page.locator('#phrase').fill('incorrect fictional phrase'); await page.locator('#unlock').click(); await state(page,'Could not unlock');
  assert.ok((await stored(page)).record); assert.equal(await page.locator('#snapshot').isVisible(),false);
  pass('Wrong phrase leaves ciphertext intact and balances hidden');
  phase='offline reload'; await context.setOffline(true); await page.reload(); await unlock(page);
  assert.equal(await page.locator('#captured-at').textContent(),captured);
  assert.ok((await page.locator('#connection').textContent()).includes('Server unavailable'));
  const cached = await page.evaluate(async () => {
    const names=await caches.keys(); return Promise.all(names.map(async name=>({name,paths:(await (await caches.open(name)).keys()).map(r=>new URL(r.url).pathname)})));
  });
  assert.equal(cached.length,1); assert.equal(cached[0].paths.length,10);
  for (const p of cached[0].paths) assert.ok(!/\/access$|\/options$|\/snapshot$|\/changes$|\/approve$|\/login$|\/logout$/.test(p));
  assert.equal(await page.evaluate(async()=> (await navigator.serviceWorker.getRegistration()).scope),origin+'/saved-view/');
  pass('Offline reload/unlock preserves capture time; scoped worker caches ten public assets only');
  phase='cross window'; const other=await context.newPage(); await other.goto(origin+'/saved-view/'); await unlock(other);
  await other.locator('#forget').click(); await state(page,'removed in another window');
  assert.equal(await page.locator('#snapshot').isVisible(),false); assert.equal((await stored(page)).record,null);
  await other.close(); pass('Removal clears actual IndexedDB and blanks another open window');
  await context.setOffline(false); await save(page); await unlock(page);
  await page.evaluate(()=>__fictionalClock(300001)); await state(page,'5 minutes');
  assert.equal(await page.locator('#snapshot').isVisible(),false);
  await page.evaluate(()=>__fictionalClock(86400001));
  await page.locator('#phrase').fill(PHRASE); await page.locator('#unlock').click(); await state(page,'lifetime ended');
  assert.equal((await stored(page)).record,null); await page.evaluate(()=>__fictionalClock(0));
  pass('Idle relock and chosen retention expiry remove access in actual browser storage');
  phase='owner reset'; await save(page); await context.request.post(origin+'/fixture/reset');
  await page.locator('#phrase').fill(PHRASE); await page.locator('#unlock').click(); await state(page,'Access was denied');
  assert.equal((await stored(page)).record,null);
  pass('Owner reset invalidates the signed cookie and removes an existing saved copy');
  await context.request.post(origin+'/fixture/login'); await page.reload(); await approve(page,false); await save(page); await unlock(page);
  assert.ok((await page.locator('#accounts').textContent()).includes('check time unknown'));
  assert.ok((await page.locator('#changes').textContent()).includes('Not enough recorded history') || (await page.locator('#changes').textContent()).includes('Comparisons need'));
  await page.setViewportSize({width:1280,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(REVIEW,'saved-view-desktop.png'),fullPage:true});
  pass('History opt-out keeps unknown freshness honest and preserves desktop layout');
  phase='logout cleanup page'; await page.goto(origin+'/saved-view/cleared');
  await page.waitForFunction(()=>document.getElementById('clear-status').textContent.includes('Saved copy removed'));
  assert.equal((await stored(page)).record,null);
  await page.goto(origin+'/saved-view/'); await save(page); await unlock(page);
  pass('Original logout cleanup page executes strict-CSP storage removal');
  const oldCookie=(await context.cookies()).find(c=>c.name==='mypocket_session').value;
  phase='logout'; await page.locator('#logout').click(); await state(page,'Signed out everywhere');
  assert.equal((await stored(page)).record,null); assert.equal(await page.locator('#snapshot').isVisible(),false);
  const rejected=await context.request.get(origin+'/saved-view/access',{headers:{Cookie:'mypocket_session='+oldCookie}});
  assert.equal(rejected.status(),401); pass('Sign-out clears local storage and invalidates copied prior session cookies');
  assert.deepEqual(unexpected,[]); assert.deepEqual(errors,[]);
  report.status='passed'; report.browser=browser.version(); report.page_errors=errors;
  pass('No external-origin requests or JavaScript page errors');
})().catch(error=>{report.status='failed';report.phase=phase;report.error=error.message;console.error('FAIL '+phase+': '+error.message);process.exitCode=1;})
.finally(async()=>{ await fs.mkdir(REVIEW,{recursive:true}); await fs.writeFile(path.join(REVIEW,'saved-view-browser-report.json'),JSON.stringify(report,null,2)+'\n'); if(context)await context.close();if(browser)await browser.close();if(server)server.kill('SIGTERM'); });
