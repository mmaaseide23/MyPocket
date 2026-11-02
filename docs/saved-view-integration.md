# Optional encrypted saved view

The implementation is complete behind disabled defaults. It has been exercised
with invented records in in-memory SQLite and an isolated Chromium browser.
No live financial data, credentials, deployment settings or running app were used
or changed. The earlier fictional demo remains separate and unchanged.

## Activation and consent are separate

`saved_view_enabled` defaults to false. The normal app mounts no saved-view routes,
shows no new navigation and creates no feature tables with both feature flags off.
A future reviewed rollout requires an explicit HTTPS `saved_view_origin`; plain
HTTP, a different host and cross-origin requests are rejected in enabled mode.
The HTTP loopback exception exists only on the synthetic test factory and cannot
be selected by production configuration.

Enabling routes is not consent to save data. The approval screen has no account,
retention or acknowledgment preselected. It requires selected accounts, a lifetime
between one hour and seven days, and explicit acknowledgments of fields, phrase
handling and offline-revocation limits. Save is a separate action. Online-only,
Forget, revoke and sign-out remain available.

The field policy includes generic numbered labels, types, selected USD balances,
opaque references and source/check timestamps. Account names/numbers, transactions,
holdings, personal details, provider payloads, tokens and passwords are excluded.
The approval choices query only account IDs/types/currency; internal IDs are not
included in the saved payload. Unsupported accounts fail closed.

## Encryption, shell and lifecycle

A separate strong 12–256 character phrase derives a nonextractable AES-256-GCM
key using PBKDF2/SHA-256 with 600,000 iterations. Fresh salts/IVs are used for each
save. Only ciphertext, opaque policy identifiers, salt/IV and save/expiry metadata
persist in the separate IndexedDB namespace. Metadata is authenticated; changing
retention or owner identifiers cannot extend access. The phrase and key are not
saved or sent to the server. Forgotten phrases cannot be recovered.

The explicit Save action first checks approval, prepares the scoped worker and
then fetches the minimal no-store projection. The worker caches ten exact public
shell assets under `/saved-view/`; it does not cache API/auth responses, queries,
other origins, arbitrary navigation or non-GET requests. There is no background
provider sync, keepalive or paid/cloud dependency. The shell keeps the existing
sky/glass styling and inserts returned text without HTML evaluation.

Lock clears displayed values and in-memory references. Page hide/exit, restored
pages and five minutes idle relock the view. Cross-window signals and periodic
storage checks handle removal/replacement. Revision checks cancel pending saves
and unlocks after lock/clear. Expiry is checked on unlock and while displayed;
closed apps cannot promise background erasure. Browser eviction can remove the
copy, and retention depends on the device clock.

An unreachable server permits an existing unexpired copy and shows that access
cannot be checked. Observed denial or changed owner/generation removes the copy.
A failed deletion remains locked and reports that browser website data must be
cleared; it does not claim successful removal. No web application can remotely
erase a disconnected phone immediately or guarantee physical memory zeroization.

## Sessions and removal

Saved-view mode issues Secure, HttpOnly, SameSite=Lax session cookies and binds
signed tokens to an opaque server-side epoch and singleton owner identity.
Legacy cookies cannot authorize the enabled mode. Sign out everywhere invalidates
the epoch and approval; owner reset invalidates old cookies and approval. Initial
login after reset establishes a fresh scope. This remains a single-owner app.

The saved-view sign-out/revoke flow removes this device's copy before contacting
the server. Original app logout redirects through a strict-CSP cleanup page that
also clears storage and broadcasts removal. Remote effects require connectivity;
other offline devices retain access until expiry/removal or their next successful
access check. The UI distinguishes failed/unconfirmed remote removal.

Approval, revoke, logout, login and setup use exact-origin checks in enabled mode.
Saved-view responses use no-store and a CSP without inline/eval allowances.
Public assets contain no user values or cookies. HTTPS proxy forwarding must be
trusted only from the intended proxy. Existing unrelated application pages still
use the legacy inline/eval CSP; same-origin application code remains trusted and
could access an unlocked view. This patch does not create a separate-origin vault
or rewrite the rest of the app.

## Freshness and change explanations

`saved_view_insights_enabled` is separately disabled by default. Collection also
requires an active owner approval with its optional history checkbox selected.
Only selected-account balance observations are retained. Attempts, completion,
outcome, observed balances and provider-as-of times are distinct. Current adapters
do not supply provider-as-of timestamps, so these remain unknown. A failed,
partial or interrupted check never becomes a completed observation.

Capture and device save do not refresh underlying data. Existing `last_synced`
values are not backfilled into invented history. Missing connection mappings stay
unknown. A balance differing from its last completed observation is unverified.
History opt-out/revocation removes feature rows, and changed selections remove
unselected balance observations. Consent changes serialize with in-progress
checks so late completion cannot recreate revoked history; server confirmation
may wait for the current check to finish.

Comparison history retains up to 30 days, pruned after completed checks. Cleanup
is not guaranteed while checks stop and is not secure erasure of SQLite pages or
backups. This limitation is stated in the optional history acknowledgment.

The online explanation shows each account's two most recent completed check
windows, balance difference and signed net-worth contribution. Unknown or missing
history stays explicit. Different account windows are not combined into a fake
daily return. Comparisons are fetched while connected and are not stored in the
phone snapshot. Spending, deposits, transfers and market causes are not attributed;
the earlier daily-gain discrepancy is not claimed to be solved.

## Validation and remaining rollout steps

Run only from an isolated source checkout without a live `.env` or database:

```sh
PYTHONDONTWRITEBYTECODE=1 DATABASE_URL=sqlite:// python -m pytest -q
node --test tests/saved_view_client.test.mjs tests/offline_storage.test.mjs tests/offline_worker.test.mjs
ruff check .
node tests/saved_view_browser.cjs
```

The browser runner uses installed Playwright/Chromium and the project Python;
optional `PLAYWRIGHT_MODULE`, `PLAYWRIGHT_CHROMIUM_EXECUTABLE` and
`MYPOCKET_TEST_PYTHON` select their locations. It launches a fresh sandboxed browser
and an ephemeral loopback fixture, blocks providers/Keychain and external origins,
and cleans up both processes. Reports/screenshots stay in ignored `review/`.

Synthetic coverage includes strict approval, safe SQL projection, all three mocked
adapters, history opt-out, session reset/revocation, CSRF/origin/cookie/CSP checks,
Python-to-Web-Crypto compatibility, actual IndexedDB encryption, scoped caching,
offline reload/unlock, idle/expiry, removal across windows and mobile/desktop fit.

Still required: a physical iPhone test of this new personal protocol, followed by
explicit approval for real-data rollout/migration and the chosen account/retention/
unlock/history policies. The earlier successful iPhone test covered the fictional
demo only. No production migration or real-data activation has been attempted.
