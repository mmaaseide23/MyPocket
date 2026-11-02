import {SavedViewClient, SavedViewStore, SnapshotError} from './client.mjs';

const $ = id => document.getElementById(id);
const store = new SavedViewStore();
const client = new SavedViewClient(store, {enabled: true, onLock: wipe});
const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('mypocket-personal-lock-v1') : null;
let generation = 0, expiryTimer, checking = false;
const date = v => v ? new Date(v).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}) : 'Unknown';
const money = v => v === null ? 'Unknown' : new Intl.NumberFormat(undefined, {style: 'currency', currency: 'USD'}).format(v / 100);
const messages = {
  empty: 'No saved copy. Choose a policy while connected, then save one.',
  expired: 'Your chosen lifetime ended. The copy was removed; save again while connected.',
  access_denied: 'Access was denied or changed. This saved copy cannot be opened. Sign in and review your policy.',
  online_required: 'Saving needs a connection and an approved policy. An existing valid copy can still be unlocked offline.',
  phrase_length: 'Use a separate phrase between 12 and 256 characters.',
  unlock_failed: 'Could not unlock. Check the phrase, or forget this copy and save again.',
  storage_unavailable: 'Device storage failed. Removal cannot be confirmed; clear this site’s website data in browser settings.',
  shell_unavailable: 'The offline shell could not be prepared. Reconnect and try again.',
  crypto_unavailable: 'Secure device storage is unavailable. Use a supported browser over HTTPS.',
  cancelled: 'The view was locked or changed in another window. Unlock again.',
  idle: 'Locked after 5 minutes without activity.',
};
const message = error => messages[error.code] || 'The saved view is unavailable. Reconnect and review the policy, or remove the copy.';
function status(text, state = 'Locked') { $('status').textContent = text; $('state').textContent = state; }
function wipe() {
  clearTimeout(expiryTimer); $('snapshot').hidden = true; $('phrase').value = '';
  for (const id of ['net-worth','cash','investments','debt','captured-at','connection','accounts','sources','saved-at','expires-at','changes']) $(id).replaceChildren();
}
function lock(text = 'Saved view locked.', broadcast = true) {
  generation++; client.lock(); status(text); if (broadcast) channel?.postMessage('lock');
}
function busy(value) { for (const id of ['save','unlock','approve']) $(id).disabled = value; }
function row(parent, text, className = '') { const node = document.createElement('li'); node.textContent = text; node.className = className; parent.append(node); return node; }
function connection() {
  $('connection').textContent = client.connection === 'allowed'
    ? 'Connected. These are still the saved balances; capture and save do not refresh accounts.'
    : 'Server unavailable. Showing the saved copy; remote access changes cannot be checked.';
}
async function changes(snapshot, operation) {
  row($('changes'), 'Comparisons need a connection and two completed checks.');
  if (client.connection !== 'allowed') return;
  try {
    const response = await client.response('/saved-view/changes');
    if (operation !== generation || !client.snapshot) return;
    if (!response || response.status >= 500) return;
    if (!response.ok || response.redirected || response.type === 'opaqueredirect') { await forget(messages.access_denied); return; }
    const result = await response.json();
    if (operation !== generation || !client.snapshot) return;
    if (result.owner_scope !== snapshot.owner_scope || result.generation !== snapshot.generation) { await forget(messages.access_denied); return; }
    if (!Array.isArray(result.accounts) || result.accounts.length > 20) return;
    $('changes').replaceChildren();
    for (const item of result.accounts) {
      if (!snapshot.accounts.some(a => a.ref === item.ref)) continue;
      const text = item.status === 'compared'
        ? `${item.label}: ${money(item.balance_change_minor)} balance change; ${money(item.net_worth_contribution_minor)} toward your selected total. ${date(item.previous_observed_at)} → ${date(item.current_observed_at)}. Cause not determined.`
        : `${item.label}: ${item.status === 'current_value_unverified' ? 'Current value is not verified by a completed check.' : 'Not enough recorded history yet.'}`;
      row($('changes'), text, 'saved-small');
    }
  } catch { /* Optional online explanation never changes the saved balances. */ }
}
function render(snapshot) {
  const total = types => {
    const accounts = snapshot.accounts.filter(a => types.includes(a.type));
    return accounts.some(a => a.balance_minor === null) ? null : accounts.reduce((sum, a) => sum + a.balance_minor, 0);
  };
  const cash = total(['checking','savings']), investments = total(['brokerage','ira']), debt = total(['credit']);
  $('net-worth').textContent = money([cash,investments,debt].includes(null) ? null : cash + investments - debt);
  $('cash').textContent = money(cash); $('investments').textContent = money(investments); $('debt').textContent = money(debt);
  $('captured-at').textContent = `Snapshot captured ${date(snapshot.captured_at)}`; connection();
  for (const a of snapshot.accounts) {
    const li = row($('accounts'), '', 'saved-account'), name = document.createElement('span'), amount = document.createElement('strong');
    name.textContent = `${a.label} · ${a.freshness === 'recorded' ? 'balance observed ' + date(a.observed_at) : a.freshness === 'unverified' ? 'current value unverified' : 'check time unknown'}`;
    amount.textContent = money(a.balance_minor); li.append(name, amount);
  }
  if (!snapshot.sources.length) row($('sources'), 'No completed connection observations recorded. Provider timestamps are unknown.');
  for (const source of snapshot.sources) {
    const name = {teller:'Bank connection',plaid:'Bank connection',etrade:'Investment connection'}[source.provider];
    row($('sources'), `${name} · ${source.connection_state}. Last attempt: ${date(source.last_attempt_at)} (${source.attempt_outcome}). Last successful check: ${date(source.last_successful_check_at)}. Provider data as of: ${date(source.provider_as_of)}.`, 'saved-small');
  }
  $('saved-at').textContent = `Saved to this device ${date(client.record.saved_at)}`;
  $('expires-at').textContent = `Copy expires ${date(client.record.expires_at)}`;
  $('snapshot').hidden = false; status('Saved view unlocked. Read-only.', 'Unlocked');
  expiryTimer = setTimeout(() => { void forget(messages.expired).catch(() => {}); }, Math.max(0, Date.parse(client.record.expires_at) - Date.now()));
  void changes(snapshot, generation);
}
async function prepareShell() {
  if (!globalThis.isSecureContext || !navigator.serviceWorker) throw new SnapshotError('crypto_unavailable');
  let timer;
  try {
    await Promise.race([(async () => {
      const registration = await navigator.serviceWorker.register('/saved-view/sw.js', {scope:'/saved-view/'});
      if (registration.scope !== location.origin + '/saved-view/') throw new Error('scope');
      const worker = registration.active || registration.installing || registration.waiting;
      if (!worker) throw new Error('worker');
      await new Promise((resolve,reject) => {
        const check = () => {
          if (worker.state === 'activated') { worker.removeEventListener('statechange',check); resolve(); }
          if (worker.state === 'redundant') { worker.removeEventListener('statechange',check); reject(new Error('install')); }
        }; worker.addEventListener('statechange',check); check();
      });
      await new Promise((resolve,reject) => {
        const port = new MessageChannel(); port.port1.onmessage = event => { port.port1.close(); event.data?.ready ? resolve() : reject(new Error('cache')); };
        worker.postMessage({type:'prepare-shell'}, [port.port2]);
      });
    })(), new Promise((_,reject) => { timer = setTimeout(() => reject(new Error('timeout')),20000); })]);
  } catch { throw new SnapshotError('shell_unavailable'); } finally { clearTimeout(timer); }
}
async function act(action) {
  const phrase = $('phrase').value; lock('Preparing saved view…', false); const operation = generation; busy(true);
  try {
    if (phrase.length < 12 || phrase.length > 256) throw new SnapshotError('phrase_length');
    if (action === 'save') {
      // Verify approval before installing anything for offline use.
      if (!await client.access()) throw new SnapshotError('online_required');
      if (operation !== generation) return;
      await prepareShell(); if (operation !== generation) return;
      await client.save(phrase); if (operation !== generation) return;
      $('approval-status').textContent = 'A copy was saved using your approved policy. Review choices here to change it.';
      channel?.postMessage('changed'); status('Last synced copy saved. Enter the same phrase to unlock.');
    } else {
      const snapshot = await client.unlock(phrase);
      if (operation !== generation || document.hidden) { client.lock(); return; }
      render(snapshot);
    }
  } catch (error) { if (operation === generation) { client.lock(); status(message(error)); } }
  finally { busy(false); }
}
async function forget(text = 'Saved copy removed from this device.', broadcast = true) {
  lock('Removing saved copy…', false); const operation = generation;
  try { await client.forget(); if (operation === generation) status(text, 'Empty'); }
  catch (error) { if (operation === generation) status(message(error)); throw error; }
  finally { if (broadcast) channel?.postMessage('clear'); }
}
async function post(path, body = {}) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch('/saved-view/' + path, {method:'POST', credentials:'same-origin', cache:'no-store', redirect:'manual',
      headers:{'Content-Type':'application/json','Accept':'application/json'}, body:JSON.stringify(body), signal:controller.signal});
    if (!response.ok || response.redirected || response.type === 'opaqueredirect') throw new SnapshotError('access_denied');
    return await response.json();
  } catch (error) { throw error instanceof SnapshotError ? error : new SnapshotError('online_required'); }
  finally { clearTimeout(timer); }
}
async function loadOptions() {
  try {
    const response = await client.response('/saved-view/options');
    if (!response?.ok || response.redirected) { $('approval-status').textContent = 'Connect and sign in to choose a policy. Existing copies may still unlock offline.'; return; }
    const value = await response.json();
    if (!Array.isArray(value.accounts) || value.accounts.length > 100) return;
    $('account-options').replaceChildren();
    for (const account of value.accounts) {
      const label = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
      label.className = 'saved-choice'; input.type = 'checkbox'; input.name = 'account'; input.value = String(account.id); text.textContent = account.label;
      label.append(input,text); $('account-options').append(label);
    }
    $('history').disabled = !value.history_available;
    $('history-help').textContent = value.history_available ? 'Optional. Leave unchecked to avoid keeping comparison history.' : 'Comparison-history collection is disabled in this installation.';
    $('approval-status').textContent = 'Nothing is preselected. Choose accounts and a lifetime, then review each acknowledgment.';
  } catch { $('approval-status').textContent = 'Policy choices are unavailable while disconnected.'; }
}
$('approval-form').addEventListener('submit', async event => {
  event.preventDefault();
  const ids = [...document.querySelectorAll('input[name=account]:checked')].map(input => Number(input.value));
  if (!ids.length) { $('approval-status').textContent = 'Select at least one account.'; return; }
  busy(true);
  try {
    await forget('Reviewing the new policy…');
    const result = await post('approve', {account_ids:ids, field_set:'generic-balances-v1', retention_seconds:Number($('retention').value),
      history_enabled:$('history').checked, confirm_fields:$('confirm-fields').checked,
      confirm_offline_limit:$('confirm-offline').checked, confirm_phrase:$('confirm-phrase').checked});
    client.denied = false;
    $('approval-status').textContent = `Policy approved for ${result.retention_seconds / 3600} hours. No copy has been saved yet.`;
    $('approval-panel').open = false;
    status('Policy approved. Enter a separate phrase and choose Save last synced copy.');
  } catch (error) { $('approval-status').textContent = message(error); }
  finally { busy(false); }
});
async function remoteRemoval(action) {
  try {
    await forget(); await post(action);
    status(action === 'logout' ? 'Signed out everywhere. This device’s copy was removed.' : 'Saved-view access revoked. This device’s copy was removed.', 'Empty');
    $('approval-status').textContent = 'No active saved-view policy. Sign in and approve a new policy to save again.';
  } catch { status('This view is locked. Remote removal could not be confirmed. Reconnect to finish; if local removal failed, clear this site’s website data.'); }
}
$('unlock').addEventListener('click', () => act('unlock')); $('save').addEventListener('click', () => act('save'));
$('lock').addEventListener('click', () => lock()); $('forget').addEventListener('click', () => { void forget().catch(() => {}); });
$('revoke').addEventListener('click', () => remoteRemoval('revoke')); $('decline').addEventListener('click', () => remoteRemoval('revoke'));
$('logout').addEventListener('click', () => remoteRemoval('logout'));
document.addEventListener('visibilitychange', () => { if (document.hidden) lock('Locked while hidden.'); });
window.addEventListener('pagehide', () => lock()); window.addEventListener('pageshow', event => { if (event.persisted) lock(); });
for (const name of ['pointerdown','keydown','touchstart']) document.addEventListener(name, () => { client.lastActivity = Date.now(); }, {passive:true});
if (channel) channel.onmessage = event => {
  if (event.data === 'clear') void forget('Saved copy removed in another window.', false).catch(() => {});
  if (['changed','lock'].includes(event.data)) lock('Saved view changed or locked in another window.', false);
};
async function recheck() {
  if (!client.snapshot || checking) return; checking = true; const operation = generation;
  try { await client.access(client.record); if (operation === generation && client.snapshot) connection(); }
  catch (error) { if (operation === generation) lock(message(error)); } finally { checking = false; }
}
window.addEventListener('online', recheck); setInterval(recheck,30000);
setInterval(async () => { const operation = generation; try { await client.tick(); } catch (error) { if (operation === generation) lock(message(error)); } },5000);
try {
  const initial = await client.status();
  if (!generation) status(initial === 'locked' ? 'An encrypted copy is saved. Enter its separate phrase to unlock.' : messages[initial], initial === 'locked' ? 'Locked' : 'Empty');
} catch (error) { status(message(error)); }
void loadOptions();
