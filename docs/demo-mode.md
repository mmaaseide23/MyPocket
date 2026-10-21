# Fictional demo mode

After unlocking MyPocket, choose **Show demo** in the bottom-right corner. The
button sits above the phone navigation. A **Fictional demo** banner identifies
the sample accounts, transactions, holdings, charts, connection status, and
Planning values. You can try the planner and its contribution changes normally.

Demo mode persists across navigation and reloads in that browser for 30 days.
**Exit demo** asks for confirmation and returns to the normal unlock screen
before real information can be shown. It does not create a public sharing link.

Demo requests are handled by a separate app using deterministic, memory-only
fixtures. They do not query the configured financial database or call providers.
Unknown demo routes fail closed. Sync is simulated; connects, imports, account
deletion, and all other financial mutations are blocked. Existing independent
background syncing is unchanged.

Pages and API responses are not cached. Changing modes clears the old page,
aborts pending app fetches, reloads other tabs through a mode-only notification,
and clears page content before back/forward caching. Stale API requests that
claim the wrong mode are rejected. No financial values are stored in the mode
cookie or browser notification. Mode changes also request browser cache clearing.
Reload any older MyPocket tabs once after installing this feature so they receive
the mode safeguards. This does not erase screenshots, downloads, or browser
records created before entering demo mode.

Tests use poisoned real database/provider dependencies and fictional browser
fixtures. Never use an authenticated real financial page as a test fixture.
