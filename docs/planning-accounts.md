# Investment-account planning

Planning automatically loads the combined saved total of active connected USD brokerage and IRA accounts. Each account’s stored total is counted once, including investment cash. Holdings are never queried or added to this total. Checking, savings, credit cards, archived accounts, CSV-only imports and non-USD accounts are outside this scope. Known duplicate source/account identities keep the most recently synced copy; exclusions are explained in the page.

There is no manual starting amount, example mode, individual holding selection, per-holding return, or contribution-allocation editor. Enter one **Assumed average annual return** for the whole investment-account total, choose years, and optionally add a total monthly contribution. Under More options, a contribution schedule can override that starting amount at selected projection years and months, including $0 pauses and later restarts. Inflation is editable. The rate begins blank: no personalized rate or historical average is inferred. The starting amount never falls back to fictional values.

All eligible account totals must be present and finite before projecting. Missing totals pause the whole projection instead of silently using a partial sum. Zero is a valid starting total. Negative totals and totals over the calculator’s $1 billion input limit display an unsupported-state message. Saved totals older than 24 hours, unknown sync dates, and future sync dates have explicit warnings. Displayed times are recorded account sync times, not guaranteed valuation/price timestamps. Reload reads stored values only and does not initiate a bank sync.

The return is a portfolio-wide effective annual assumption, not a simple average of ticker returns or a measured cashflow-adjusted historical return. Monthly rate is `(1 + annual return)^(1/12) - 1`. Returns are reinvested, fixed-dollar contributions arrive at month-end, and today’s purchasing power divides the ending value by `(1 + inflation)^years`. The chart distinguishes starting value plus future deposits from projected growth; it does not imply that the saved account total equals historical contributions.

This is an investment-account projection, not complete net worth. It models no volatility, withdrawals, taxes, fees, trading, rebalancing, or future allocation changes. The single rate applies to cash and investments together. Results are illustrations, not forecasts or recommendations.

## Contribution changes

The starting monthly contribution applies until the first scheduled change. Year 1,
month 1 means the first projection month; year 2, month 1 means month 13. A change
applies to the deposit at that month's end and remains in force until the next
change. A change at year 1, month 1 overrides the starting monthly amount.

Entries are applied in date order, regardless of their editor order. Duplicate
start months, non-integer/out-of-range dates, and negative or nonfinite amounts
block the projection. Years 1–60 and months 1–12 are supported, with amounts from
$0 through $1 billion. Valid changes past the chosen horizon remain editable but
are explicitly excluded, with a visible result warning and row notes. Extending
the horizon includes them again; no entry is silently dropped.

Amounts are fixed nominal dollars between changes, with no automatic inflation
increase. Cumulative deposits, compound growth, yearly chart/table values and
inflation-adjusted end value all derive from the same monthly simulation. The
starting-value-plus-deposits total includes the initial saved investment value;
it does not describe historical contributions. Schedule entries remain in page
memory, never add requests or persistence, and are cleared on page exit.

## Privacy and implementation

The page shell stays independent of database code. The browser automatically requests `/api/planning/accounts` from the same app under its existing authentication. The endpoint is read-only, requires the same-origin custom request header, rejects cross-site origins, sends `Cache-Control: no-store`, and returns only the combined value, account counts, sync-date range and warning codes. It does not return account identifiers, individual account balances, holdings or credentials. The obsolete holdings endpoint and client editor were removed.

No plan inputs or account payloads are logged, submitted to outside services, encoded in URLs or persisted by the planner. Values exist in page memory only. Page exit clears stored values, chart datasets and assumptions; aborted or late responses cannot restore them. A back/forward restoration loads fresh saved totals again and resets assumptions. Existing database records, sync schedules, credentials and backups are unchanged. Browser extensions and user captures are outside the app’s control.

Tests and UI QA use isolated fictional accounts. `tests/planning_accounts_preview.py` uses an in-memory database and refuses a directory containing `.env`; it never starts the real application scheduler or integrations. Run it only in a source-only copy. Do not test by opening production account data.
