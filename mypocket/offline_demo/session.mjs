import {checkAccess, SnapshotError, SCOPE} from './store.mjs';

// Lifecycle used by the UI and by browser-independent, synthetic security tests.
export class OfflineSession {
  constructor(vault, {fetcher = globalThis.fetch, now = Date.now, onLock = () => {}} = {}) {
    // Window.fetch requires a Window receiver when called through an object method.
    // Keep the native receiver even when the session invokes its injected transport.
    this.vault = vault; this.fetcher = fetcher.bind(globalThis); this.now = now; this.onLock = onLock;
    this.generation = 0; this.lastActivity = now(); this.connection = 'unknown'; this.denied = false;
  }
  lock() { this.generation++; this.vault.lock(); this.onLock(); }
  async forget() { this.lock(); await this.vault.forget(); }
  async access() {
    const status = await checkAccess(this.fetcher, this.vault.scope);
    this.connection = status;
    if (status === 'denied') { this.denied = true; await this.forget(); throw new SnapshotError('access_denied'); }
    if (this.denied) {
      if (status !== 'allowed') throw new SnapshotError('access_denied');
      await this.forget(); this.denied = false;
    }
    return status;
  }
  async save(phrase) {
    this.lock(); const generation = this.generation;
    if (await this.access() !== 'allowed') throw new SnapshotError('online_required');
    if (generation !== this.generation) throw new SnapshotError('cancelled');
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
    let snapshot;
    try {
      const response = await this.fetcher('/offline-demo/snapshot', {
        cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller.signal,
      });
      if (!response.ok || response.redirected) throw new SnapshotError('online_required');
      snapshot = await response.json();
    } catch (error) {
      throw error instanceof SnapshotError ? error : new SnapshotError('online_required');
    } finally { clearTimeout(timer); }
    if (generation !== this.generation) throw new SnapshotError('cancelled');
    const saved = await this.vault.save(snapshot, phrase);
    if (generation !== this.generation) throw new SnapshotError('cancelled');
    return saved;
  }
  async unlock(phrase) {
    this.lock(); const generation = this.generation;
    await this.access(); // Unreachable server permits a valid previously saved snapshot.
    if (generation !== this.generation) throw new SnapshotError('cancelled');
    const snapshot = await this.vault.unlock(phrase);
    if (generation !== this.generation) { this.vault.lock(); throw new SnapshotError('cancelled'); }
    this.lastActivity = this.now();
    return snapshot;
  }
  async tick() {
    if (!this.vault.snapshot) return;
    if (this.now() - this.lastActivity >= 5 * 60 * 1000) { this.lock(); throw new SnapshotError('idle'); }
    await this.vault.assertCurrent();
  }
}

export const DEMO_OWNER = SCOPE;
