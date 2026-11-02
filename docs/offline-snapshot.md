# Offline saved-view prototype

The standalone demo shows a dated, read-only snapshot when its server is
unavailable. It reuses the existing sky/glass assets and requires no cloud host.
It is **fictional-only**: the connected application does not mount these routes,
register this worker, export account data or enable real financial caching.

The prototype includes account summaries and per-source freshness. Capture time,
device save time, successful connection checks and provider-as-of timestamps are
separate concepts. Unknown timestamps remain explicit. Saving a copy never
refreshes its underlying data, and this snapshot does not explain historical
balance changes.

## Running the fictional fixture

From a source checkout with the project's Python dependencies installed:

```sh
python -m uvicorn tests.offline_preview:app --host 127.0.0.1 --port 8765 --no-access-log
```

Open `http://127.0.0.1:8765/offline-demo/` on that computer. This fixture imports
no application settings, database, credential store or provider adapters. It
serves only the allowlisted demo assets and generated fictional responses.
The original application, routes, templates and manifest remain unchanged.

Phone testing needs a separately approved private HTTPS route to this fixture;
loopback is not a phone address. Serving the demo does not configure a tunnel,
network listener outside loopback, certificate or cloud service automatically.
Use an isolated browser/profile for automated tests.

## Data and device policy

| Item | Prototype behavior |
| --- | --- |
| Stored fields | Generic account labels, types, balances, currency, capture time and per-source check/provider timestamps |
| Excluded fields | Account numbers/IDs, personal details, transactions, positions, provider tokens, credentials and raw provider payloads |
| Summary | Cash, investments, credit balance and net worth calculated in memory |
| Unlock | A separate 12–256 character demo phrase; never an app login or provider password |
| Lifetime | One snapshot per browser profile, replaced explicitly, expiring 24 hours after saving |
| Lock | Explicit Lock, page exit/hide, owner change or 5 minutes without interaction |
| Remove | Forget saved data, observed access denial, owner mismatch, invalid metadata or expiry |

The `fictional: true` marker and fixed demo owner scope are development guards,
not authentication. Removing them is not a real-data integration.

Before enabling real financial snapshots, users must approve the included
fields, retention period and unlock method. A longer retention period extends
access after device loss or remote revocation. The existing online-only behavior
must remain available to users who decline device storage.

## Encryption and lifecycle

The entire snapshot is encrypted with AES-256-GCM before writing to IndexedDB.
Each save uses a fresh 16-byte salt and 12-byte IV. PBKDF2/SHA-256 with 600,000
iterations derives a nonextractable key from the phrase. Only ciphertext,
salt/IV, format/owner identifiers and save/expiry timestamps persist. Metadata is
authenticated as additional data, so editing expiry cannot extend a ciphertext's
validity. This uses [Web Crypto](https://developer.mozilla.org/en-US/docs/Web/API/SubtleCrypto/deriveKey).

The application never persists the phrase or derived key. Unlocking holds
plaintext in memory and the DOM; locking clears those references and displayed
values. JavaScript cannot guarantee physical memory zeroization or prevent OS
screenshots. This is not a hardware-backed key store. A strong phrase is needed
against offline guessing, and compromised same-origin code can read an unlocked
view or phrase. The fixture uses a strict CSP and text-only DOM insertion.

Revision checks prevent a pending save from recreating a removed record and a
late unlock from displaying after a lock/clear. BroadcastChannel transmits only
lock/clear/change signals; storage checks cover missed signals. There is no
analytics, provider sync, push subscription or keepalive.

Expiry is checked on opening/unlocking and while displayed. If the app stays
closed, expired ciphertext is removed when it next runs, not by a guaranteed
background job. Expiry depends on the device clock. Storage failures keep the
view locked and report when removal cannot be confirmed.

## Access and cache boundaries

A reachable server's denied, revoked or mismatched-owner response clears the
saved record before unlocking. An unreachable server permits a valid existing
snapshot and displays that access could not be checked. **A disconnected device
cannot discover a new remote revocation.** It can retain offline access until
expiry or local removal. Empty/expired storage never becomes invented user data.

The worker is scoped to `/offline-demo/` and caches nine exact public shell
assets. Registration happens only on explicit Save. API/access/snapshot/auth
responses, unknown paths, query strings, other origins and non-GET requests are
not cached or intercepted. Redirected assets fail installation. Fixture API
requests use no-store and omit credentials. The shell cache is checked before
each save so missing assets can be restored while connected.

The first successful load/save requires the server to be reachable. Workers
require a [secure context and matching scope](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API/Using_Service_Workers).
Browser storage can be cleared or [evicted](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria);
a Home Screen icon does not guarantee permanent offline availability.

## Validation

Run the full Python suite only from an isolated source checkout without a live
`.env` or financial database. The test guard forbids provider sockets and Keychain
access and uses synthetic databases.

```sh
PYTHONDONTWRITEBYTECODE=1 DATABASE_URL=sqlite:// python -m pytest -q
node --test tests/offline_storage.test.mjs tests/offline_worker.test.mjs
ruff check .
python -m scripts.preview_offline
```

`tests/offline_browser.cjs` additionally exercises actual IndexedDB, encryption,
wrong/correct unlock, reload/lock, expiry, removal, known revocation, cross-window
clearing, scoped caches, offline navigation, empty states and unknown timestamps.
It starts its own ephemeral loopback fixture and uses an isolated Chromium
profile with the browser sandbox enabled. It requires an available Playwright
installation and Chromium executable; optional `PLAYWRIGHT_MODULE`,
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` and `MYPOCKET_TEST_PYTHON` select those tools.

```sh
node tests/offline_browser.cjs
```

Generated HTML, screenshots and test reports go under ignored `review/` and are
not committed. The static HTML preview contains only invented values, with no
scripts, networking or storage. The fictional offline flow has also been
manually confirmed on an iPhone with the Mac unavailable; that does not validate
real-data integration or guarantee identical behavior on every iOS version.

## Real-data integration still required

The optional integration is described in
[saved-view-integration.md](saved-view-integration.md). Its approval, export,
freshness/history, shell and security flow is implemented and synthetic-tested,
with real-data activation disabled. The following original integration checklist
is now covered by that opt-in code; real rollout and a new iPhone test remain.

1. Project only approved fields through an authenticated export; bind an opaque
   owner scope and revocation version to the current user.
2. Implement an authenticated, no-store access check that distinguishes denied
   access from server unavailability.
3. Integrate logout, reset and user switching with immediate blanking and local
   snapshot removal across windows. Server-only logout cannot erase a disconnected
   browser; the fixture has no real logout route.
4. Review the connected application's CSP, origin boundary, session revocation
   and user isolation before allowing actual financial data onto the phone.

This commit does not migrate storage, add multiuser support or activate any of
those capabilities.
