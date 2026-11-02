import {SavedViewStore} from './storage.mjs';
const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('mypocket-personal-lock-v1') : null;
channel?.postMessage('clear');
try {
  await new SavedViewStore().clear();
  document.getElementById('clear-status').textContent = 'Saved copy removed from this device. Other offline copies expire or clear when they reconnect.';
} catch {
  document.getElementById('clear-status').textContent = 'Removal could not be confirmed. Clear this site’s website data in browser settings before sharing this device.';
} finally { channel?.close(); }
