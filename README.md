# MyPocket

Local-first personal finance dashboard. Connect Citi via Teller, E*TRADE
brokerage / Roth IRA via the official API. Tracks balances, transactions,
spending by category, holdings, gains, dividends, and savings rate. Dark-mode,
mobile-friendly UI.

Runs entirely on your own machine. No third party sees your data except the
official provider APIs you explicitly authorize.

## Setup

```bash
uv sync
cp .env.example .env    # then edit .env with your keys
```

## Run (laptop only)

```bash
uv run uvicorn mypocket.main:app --reload --port 8000
```

Open <http://localhost:8000>. First visit lands on `/setup` — pick a passcode
that gates all subsequent access. The cookie lasts 30 days per device.

## Run (phone access via Tailscale)

The recommended way to reach the app from your phone without exposing it to
the public internet.

1. Install Tailscale on the laptop and phone, both signed into the same tailnet:
   <https://tailscale.com/download>
2. Bind the app to all interfaces so Tailscale can reach it. While you're
   home, also wrap it in `caffeinate` so the laptop stays awake:
   ```bash
   caffeinate -i uv run uvicorn mypocket.main:app --host 0.0.0.0 --port 8000
   ```
3. On your phone, browse to `http://<laptop-tailnet-name>:8000` (or the
   Tailscale IP from `tailscale ip -4`).
4. Safari share menu → "Add to Home Screen". You'll get a real app icon and
   standalone window (manifest + icon are wired up).

When done, swap back to `--host 127.0.0.1` so the app isn't reachable from
your LAN.

## Auto-sync

A background task syncs Teller + E*TRADE every 6 hours while the server is
running. Override with `MYPOCKET_SYNC_INTERVAL_SECONDS=900 uv run uvicorn …`
for 15-minute sync. There's also a manual "Sync now" button in the nav.

## Security model

- **Passcode** gates every route except `/login`, `/setup`, `/healthz`, and
  `/static/*`. Scrypt-hashed (N=16384, r=8, p=1). Reset by deleting the
  `appconfig` row in `data/mypocket.db`.
- **Session cookie** is HMAC-signed with a 32-byte key in macOS Keychain.
  Expires after 30 days.
- **Access tokens** (Teller + E*TRADE) are AES-256-GCM encrypted at rest in
  SQLite via a transparent SQLAlchemy TypeDecorator. Master key in Keychain.
- **File perms** `0600` on `.env`, `teller/*.pem`, `data/mypocket.db`.
- **Rate limiting** on `/login` (10 attempts per IP per 5 min → 429).
- **CSP / X-Frame-Options / X-Content-Type-Options** set via middleware.
- **No CDN dependency** — Tailwind/Alpine/Chart.js are self-hosted under
  `mypocket/static/vendor/`. Tailwind is pre-built into a single CSS file
  (no runtime JIT compilation on the phone).

## Connecting accounts

**Citi** (or any Teller-supported bank): sign up at <https://teller.io>,
download the mTLS cert + key into `teller/`, set `TELLER_APPLICATION_ID` in
`.env`, then use the Connect page to launch Teller Connect.

**E*TRADE** brokerage + Roth IRA: apply at
<https://developer.etrade.com/getting-started>. When keys arrive, set
`ETRADE_CONSUMER_KEY` / `ETRADE_CONSUMER_SECRET` in `.env` and run the OAuth
flow on the Connect page.

## Analytics model

- **Net worth** = `cash + invested − credit owed`
  - Cash: checking + savings balances
  - Invested: brokerage + IRA market value
  - Credit owed: outstanding card balance (treated as a liability)
- **Income** = positive transactions on **cash accounts only**. Investment
  gains, dividends paid into a brokerage account, and refunds do NOT count.
- **Spending** (gross) = sum of negative transactions on cash + credit,
  excluding transfers and investment activity.
- **Refunds** = positive amounts on credit cards + cash transactions tagged
  `Refunds` or `Reimbursements`. Tracked separately so spending stays gross.
- **Reimbursements** = Venmo / Zelle / Cash App / PayPal **inflows** — caught
  directionally during categorization (P2P outflows are still `Transfers`,
  inflows are `Reimbursements`). They offset spending but aren't income.
- **Savings rate** = `(income − spending) / income`.

## Layout

The package is organized by layer. Each top-level subpackage of `mypocket/`
serves one architectural role:

| Subpackage | Role |
|---|---|
| `core/` | Cross-cutting infrastructure (config, DB, templates, utils) |
| `domain/` | Pure business logic — no HTTP, no Jinja, no I/O concerns |
| `integrations/` | Adapters for external services (Teller, E*TRADE) |
| `routes/` | HTTP layer — one file per logical surface |
| `security/` | Auth, crypto, response headers, rate limiting |

```
mypocket/
  main.py                FastAPI app entry + lifespan + middleware/router registration
  scheduler.py           Background asyncio task that runs sync every N hours

  core/
    config.py            Settings loaded from .env
    db.py                SQLite engine, session, lightweight migrations
    templating.py        Jinja2 templates instance + "synced X ago" helper
    utils.py             to_float helper

  domain/
    models.py            Account, Transaction, Holding, AppConfig, Enrollment
    categorize.py        Rule-based transaction categorization
    analytics.py         Net worth, flows, categories, holdings P&L, dividends

  integrations/
    _oauth1.py           Minimal OAuth 1.0a HMAC-SHA1 signer (stdlib only)
    teller.py            Teller API client (mTLS + HTTP Basic auth)
    teller_sync.py       Teller → DB sync
    etrade.py            E*TRADE API client (OAuth 1.0a)
    etrade_sync.py       E*TRADE → DB sync

  routes/
    pages.py             HTML pages (/, /banking, /brokerage, /spending, …)
    api.py               JSON endpoints + manual /api/sync
    auth.py              /setup, /login, /logout
    teller.py            Teller Connect callback + sync trigger
    etrade.py            E*TRADE OAuth dance + sync trigger

  security/
    keys.py              Keychain-backed master keys (memoized)
    crypto.py            AES-256-GCM token encryption
    sqltypes.py          EncryptedString SQLAlchemy TypeDecorator
    passcode.py          scrypt passcode hashing
    session.py           HMAC-signed session cookies
    middleware.py        AuthMiddleware
    headers.py           CSP / X-Frame-Options / cache header middleware
    rate_limit.py        In-memory per-IP rate limiter
    redirects.py         safe_next() validator (prevents open redirect)

  templates/             Jinja (mobile-first; cards on phone, tables on desktop)
    _macros.html         Reusable UI: stat_card, bar_list, period_list, txn_list, …
    base.html            Top nav + drawer + window.charts helpers
    auth_base.html       Layout for /setup + /login (no nav)
    pages: overview, banking, banking_account, brokerage, brokerage_account,
           spending, transactions, connect, setup, login

  static/
    icon.svg             App icon (used as favicon + apple-touch-icon)
    manifest.webmanifest PWA manifest
    styles.css           Pre-built Tailwind output
    vendor/              Self-hosted Alpine + Chart.js

data/
  mypocket.db            SQLite, 0600 (gitignored)
teller/                  mTLS cert + private key, 0600 (gitignored)
assets/
  tailwind.css           Tailwind v4 input (build via ./scripts/build-css.sh)
scripts/
  build-css.sh           Regenerate static/styles.css from templates
bin/
  tailwindcss            Standalone CLI (gitignored; downloaded by build-css.sh)
```

**Dependency direction:** `routes/` and `integrations/` depend on `domain/` and
`core/`. `domain/` depends on `core/` only. `core/` depends on nothing else in
the project. `security/` is a leaf (used by `main.py` and a few route files).
That's the "layered architecture" guarantee — touch `core/` and you might
break the rest of the app; touch a leaf and only that file is at risk.

## Development

```bash
uv run ruff check mypocket/ --fix
uv run ruff format mypocket/
./scripts/build-css.sh         # after touching Tailwind classes in templates
```
