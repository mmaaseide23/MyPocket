import {IndexedSnapshotStore, SnapshotError, SnapshotVault} from './store.mjs';
import {OfflineSession} from './session.mjs';

const byId = id => document.getElementById(id);
const vault = new SnapshotVault(new IndexedSnapshotStore(), {onLock: () => wipeView()});
const session = new OfflineSession(vault);
const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('mypocket-fictional-lock-v1') : null;
let generation = 0, expiryTimer, accessCheckPending = false;
const date = value => value ? new Date(value).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}) : 'Unknown';
const money = value => new Intl.NumberFormat(undefined, {style: 'currency', currency: 'USD', maximumFractionDigits: 0}).format(value);
const messages = {
  empty: 'No saved snapshot. Connect to the demo and save one first.',
  expired: 'The 24-hour demo snapshot expired and was removed. Save a new copy while connected.',
  phrase_length: 'Use a demo phrase between 12 and 256 characters.',
  unlock_failed: 'Could not unlock this snapshot. Check the phrase or forget it and save a new copy.',
  access_denied: 'This demo no longer has access. The saved view cannot be opened.',
  online_required: 'The demo server is unavailable. Saving needs a connection; an existing snapshot can still be unlocked.',
  storage_unavailable: 'Device storage is unavailable. To remove an existing copy, clear this site’s website data in your browser settings.',
  crypto_unavailable: 'Secure device storage is unavailable. Open this demo over HTTPS or localhost in a supported browser.',
  shell_unavailable: 'The offline shell could not be prepared. Reconnect and try saving again.',
  idle: 'Locked after 5 minutes without activity.',
  cancelled: 'The view was locked or changed in another window. Unlock again to continue.',
};
function status(message, state = 'Locked') { byId('status').textContent = message; byId('state').textContent = state; }
function wipeView() {
  clearTimeout(expiryTimer);
  byId('snapshot').hidden = true;
  for (const id of ['net-worth', 'cash', 'investments', 'debt', 'captured-at', 'connection', 'accounts', 'sources', 'saved-at', 'expires-at']) {
    byId(id).replaceChildren();
  }
  byId('phrase').value = '';
}
function lock(message = 'Saved view locked.', broadcast = true) {
  generation++; session.lock(); wipeView(); status(message);
  if (broadcast) channel?.postMessage('lock');
}
function busy(value) { byId('save').disabled = byId('unlock').disabled = value; }
function errorMessage(error) { return messages[error.code] || 'The saved view is unavailable. Forget the demo snapshot and save a new copy.'; }
function showConnection() {
  byId('connection').textContent = session.connection === 'allowed'
    ? 'Demo server reached. This remains the saved copy; it has not refreshed.'
    : 'Server unavailable · showing your saved copy. Changes to access cannot be checked until you reconnect.';
}
function render(snapshot) {
  const total = types => snapshot.accounts.filter(account => types.includes(account.type)).reduce((sum, account) => sum + account.balance, 0);
  const cash = total(['checking', 'savings']), investments = total(['brokerage', 'ira']), debt = total(['credit']);
  byId('net-worth').textContent = money(cash + investments - debt);
  byId('cash').textContent = money(cash); byId('investments').textContent = money(investments); byId('debt').textContent = money(debt);
  byId('captured-at').textContent = `Snapshot captured ${date(snapshot.captured_at)}`;
  showConnection();
  for (const account of snapshot.accounts) {
    const row = document.createElement('li'), label = document.createElement('span'), amount = document.createElement('strong');
    row.className = 'saved-account'; label.textContent = account.label; amount.textContent = money(account.balance);
    row.append(label, amount); byId('accounts').append(row);
  }
  for (const source of snapshot.sources) {
    const row = document.createElement('li'), title = document.createElement('p'), checked = document.createElement('p'), upstream = document.createElement('p');
    title.className = 'saved-source-title'; title.textContent = source.label;
    checked.className = upstream.className = 'saved-small saved-muted';
    checked.textContent = `Last successful check: ${date(source.last_successful_check_at)}`;
    upstream.textContent = source.provider_as_of ? `Provider data as of: ${date(source.provider_as_of)}` : 'Provider data as of: unknown · timestamp not supplied';
    row.append(title, checked, upstream); byId('sources').append(row);
  }
  byId('saved-at').textContent = `Saved to this device ${date(vault.record.saved_at)}`;
  byId('expires-at').textContent = `Demo snapshot expires ${date(vault.record.expires_at)}`;
  byId('snapshot').hidden = false;
  status('Saved view unlocked. Read-only; no provider requests are made.', 'Unlocked');
  expiryTimer = setTimeout(() => forget(messages.expired), Math.max(0, Date.parse(vault.record.expires_at) - Date.now()));
}
async function prepareShell() {
  if (!globalThis.isSecureContext || !navigator.serviceWorker) throw new SnapshotError('crypto_unavailable');
  let timer;
  try {
    await Promise.race([
      (async () => {
        const registration = await navigator.serviceWorker.register('/offline-demo/sw.js', {scope: '/offline-demo/'});
        if (registration.scope !== `${location.origin}/offline-demo/`) throw new Error('wrong_scope');
        const worker = registration.active || registration.installing || registration.waiting;
        if (!worker) throw new Error('missing_worker');
        await new Promise((resolve, reject) => {
          const check = () => {
            if (worker.state === 'activated') { worker.removeEventListener('statechange', check); resolve(); }
            if (worker.state === 'redundant') { worker.removeEventListener('statechange', check); reject(new Error('install_failed')); }
          };
          worker.addEventListener('statechange', check); check();
        });
        // A registration can survive a cleared/evicted shell cache. Verify it before saving.
        await new Promise((resolve, reject) => {
          const messages = new MessageChannel();
          messages.port1.onmessage = event => {
            messages.port1.close();
            if (event.data?.ready === true) resolve(); else reject(new Error('shell_missing'));
          };
          worker.postMessage({type: 'prepare-shell'}, [messages.port2]);
        });
      })(),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('install_timeout')), 20000); }),
    ]);
  } catch { throw new SnapshotError('shell_unavailable'); }
  finally { clearTimeout(timer); }
}
async function act(action) {
  const phrase = byId('phrase').value;
  lock('Preparing saved view…', false);
  const operation = generation; busy(true);
  try {
    if (phrase.length < 12 || phrase.length > 256) throw new SnapshotError('phrase_length');
    if (action === 'save') {
      await prepareShell();
      if (operation !== generation) return;
      await session.save(phrase);
      if (operation !== generation) return;
      channel?.postMessage('changed');
      status('Fictional snapshot saved for 24 hours. Enter the same demo phrase to unlock it.');
    } else {
      const snapshot = await session.unlock(phrase);
      if (operation !== generation || document.hidden) { session.lock(); return; }
      render(snapshot);
    }
  } catch (error) {
    if (operation === generation) { session.lock(); wipeView(); status(errorMessage(error)); }
  } finally { busy(false); }
}
async function forget(message = 'Saved data removed from this device.', broadcast = true) {
  lock('Removing saved data…', false); const operation = generation;
  try {
    await session.forget();
    if (operation === generation) status(message, 'Empty');
    if (broadcast) channel?.postMessage('clear');
  } catch (error) { if (operation === generation) status(errorMessage(error)); }
}
async function recheckAccess() {
  if (!vault.snapshot || accessCheckPending) return;
  accessCheckPending = true; const operation = generation;
  try { await session.access(); if (operation === generation && vault.snapshot) showConnection(); }
  catch (error) { if (operation === generation) lock(errorMessage(error)); }
  finally { accessCheckPending = false; }
}
byId('unlock').addEventListener('click', () => act('unlock'));
byId('save').addEventListener('click', () => act('save'));
byId('lock').addEventListener('click', () => lock());
byId('forget').addEventListener('click', () => forget());
byId('phrase').addEventListener('keydown', event => { if (event.key === 'Enter' && !byId('unlock').disabled) act('unlock'); });
document.addEventListener('visibilitychange', () => { if (document.hidden) lock('Locked while the app was hidden.'); });
window.addEventListener('pagehide', () => lock('Saved view locked.'));
window.addEventListener('pageshow', event => { if (event.persisted) lock('Unlock to reopen the saved view.'); });
window.addEventListener('online', recheckAccess);
for (const name of ['pointerdown', 'keydown', 'touchstart']) document.addEventListener(name, () => { session.lastActivity = Date.now(); }, {passive: true});
if (channel) channel.onmessage = event => {
  if (event.data === 'clear') forget('Saved data was removed in another window.', false);
  if (['changed', 'lock'].includes(event.data)) lock('Saved view changed or locked in another window.', false);
};
setInterval(async () => {
  const operation = generation;
  try { await session.tick(); }
  catch (error) { if (operation === generation) lock(errorMessage(error)); }
}, 5000);
setInterval(recheckAccess, 30000);
try {
  const initial = await vault.status();
  if (generation === 0) status(initial === 'locked' ? 'An encrypted fictional snapshot is saved. Enter its demo phrase to unlock.' : messages[initial], initial === 'locked' ? 'Locked' : 'Empty');
} catch (error) { status(errorMessage(error)); }
