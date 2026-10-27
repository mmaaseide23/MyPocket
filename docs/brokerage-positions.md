# Brokerage positions

The restored Market Value hero, metric tiles, allocation chart and account list
remain in place. The lower Positions list shows identity, saved date, current
recorded value, units/basis, total gain and daily gain when available. It has no
individual-stock charts, expansion controls or range selectors. Missing metrics
are omitted; recorded zeros remain visible.

## Allocation percentages

The original ticker-only pie legend remains. Percentages appear in slice
hover/tap tooltips and when exploring the focused chart with arrow keys, Home
or End. Escape dismisses details. A screen-reader announcement describes the
selected slice; percentages are not permanent labels.

The denominator is the sum of the visible positive saved holding values in that
pie's currency, exactly matching its visible slices. Toggling a slice in the
legend recalculates the tooltip percentage. Account cash, zero-value positions
and short/negative positions are excluded. Currencies are never combined or
converted. Tooltips identify this denominator. If a position value is missing,
that currency's incomplete allocation chart remains hidden.

Percentages are rounded to one decimal; positive weights below 0.1% display
`<0.1%`, not zero. Rounded values need not sum to exactly 100.0%. Repeated symbols
across accounts include the account label. Tap details remain visible until
another interaction, without requiring continuous hover.

## Gain semantics

Daily dollar/percentage P/L uses saved E*TRADE `daysGain` / `daysGainPct`, or
explicit fictional demo data. Unknown source semantics remain omitted. These are
broker position figures, not estimates using today's share count. The aggregate
daily tile sums these saved position figures; it does not use the provider's
account-level `Totals.todaysGainLoss`. Requests currently use the documented
regular-session default. Both rows and totals are rendered from saved snapshots
on page load; background syncing does not update an already-open page. Reloading
reads the latest saved state, and a successful manual Sync reloads the page.
Neither the saved date nor successful sync time establishes quote freshness.
The exact reason for a difference from a broker app requires comparing session
and freshness; contract tests alone do not verify a real account. See the
[official portfolio definitions](https://apisb.etrade.com/docs/api/account/api-portfolio-v1.html).

Total P/L uses the saved provider figure where present. For nonnegative unit
positions with positive known basis, a missing dollar figure may be calculated
as saved value less basis. Its tooltip identifies that calculation. A saved
provider percentage can appear even if local basis is missing; a calculated
percentage requires positive known basis. Zero/negative basis never produces a
percentage through division. Basis is not lifetime contributions.

Weekly gains are intentionally omitted. The schema has no weekly P/L field and
no established cashflow-adjusted calculation. Position-value differences may
include buys, sales, transfers or splits. Daily snapshots may repeat a prior
session, omit trading days, or replace an earlier same-day snapshot. Without
quote/prior-close timestamps and complete reconciled cashflows, neither value
differences nor summed daily figures establish a trustworthy weekly return.

## Scope and privacy

Each active account uses its latest saved account snapshot. Symbols absent from
that snapshot are not carried forward. Archived accounts and exact duplicate
provider/account links are excluded, using the latest synced link. Distinct
positions are not merged by symbol. Missing totals are omitted; the aggregate
daily tile requires a common saved date. Older/unconfirmed snapshots retain
compact indicators. No records, authorizations or provider behavior are changed.

The existing read-only saved-history API and data remain available for
compatibility, but the brokerage UI no longer requests history. Rendering the
allocation chart makes no API/provider requests and adds no persistence. Existing
authentication, demo routing, cross-tab clearing and exit-to-unlock remain intact.

Tests use isolated synthetic SQLite memory fixtures. They cover positive-value
allocation denominators, mixed currencies, tiny/duplicate slices, reported and
calculated gains, missing fields/zeros, no weekly estimates and absence of stock
charts/controls. The existing snapshot and history regressions remain. Browser
QA uses `tests.positions_preview:app` in a source-only copy with a new empty
headless profile; never use a real database, `.env` or user browser profile.
