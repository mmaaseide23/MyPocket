# Financial planning

Open **Planning** in the desktop sidebar or **More → Planning** on a phone.
The starting value is the combined saved total of active connected USD investment
accounts. Enter one assumed annual return and a time horizon. **More options**
contains the starting monthly contribution, contribution changes, and inflation.
There is no manual starting-value or example mode in the live app.

A contribution change starts in a projection-relative year and month and replaces
the monthly amount from that month-end until the next change. Use $0 to pause.
Duplicate start months are blocked. Changes beyond the horizon remain editable
but are visibly excluded. All chart, yearly, and inflation-adjusted values use the
same monthly calculation.

Inputs stay in page memory and clear on exit; no new requests or persistence are
added for contribution changes. MyPocket reads saved account totals locally under
its existing login gate. This is an illustrative investment projection, not a
forecast or complete net-worth model.

See [Investment-account planning](planning-accounts.md) for scope, assumptions,
limits, privacy behavior, and calculation details.

For UI checks, run `tests.planning_accounts_preview:app` only in a source-only
workspace without `.env`. It provides fictional data in memory and never starts
provider syncs. The older `tests.planning_preview:app` command is a compatibility
alias for this isolated preview. Never use live financial pages as QA fixtures.
