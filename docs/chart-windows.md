# Chart date windows

All reporting dates use the Mac/server's local calendar. Bank/provider date-only
values stay dates; charts do not convert them into browser UTC timestamps.
Displayed ranges include both dates. Internally, a window is `[start, stop)`;
`stop` is the day after the displayed end. No future activity enters a window.
Each page pins its reporting date in chart API requests, preventing a mismatch
when a page loads across local midnight.

| View | Control | Meaning and aggregation |
| --- | --- | --- |
| Overview spending + income/spending | 7, 30, 90, 180, 365 days | Exactly N dates through today. Daily buckets for 7/30; calendar-month buckets for longer ranges. |
| Spending trend and detail | Same day choices | Same exact window; daily or monthly main chart, Monday-based weekly chart. Both cover the entire selected range. |
| Banking categories | Same day choices | Same inclusive dates as income/spending totals. |
| Banking account flow/categories | Same day choices | Flow now follows the selected range instead of always requesting 12 months. |
| Brokerage and account dividends | 3, 6, 12, 24, 36 calendar months | Exactly N calendar-month buckets, including the current month through today. |
| Allocation | No historical filter | Latest available holding snapshots, explicitly labeled; future-dated snapshots excluded. |
| Month/week comparison cards | Separate calendar comparisons | Current period to date vs matching days in the preceding period. Prior month is clamped to its actual final day. |
| Snapshot performance table | Separate 7-/30-day targets | Latest available snapshot vs nearest earlier snapshot on/before the target. Actual dates appear in each row; unavailable comparisons stay blank. |
| Financial Planning | Manual years | Unchanged: hypothetical forward projection, independent of historical chart filters. |

Month/week buckets are intersected with the selected dates. First/last partial
buckets are marked and tooltips show their exact dates. A trailing 90-day range
can touch four calendar months; only dates within those 90 days are counted.
This differs deliberately from selecting three calendar months. Zero-activity
buckets remain on the axis; month labels include the year. Detail lists use the
same bucket boundaries. Recent-transaction lists and current balances are not
historical charts and remain separate from activity-window selectors.

The previous month calculation subtracted `31 * months` and then reset to the
first day, producing extra months. Day queries also counted an extra date;
several lacked an upper bound. Trend charts independently converted day counts
to period counts, truncating or misaligning the window. Those paths now share
the same window/bucket helpers. No records, categories, authorizations, or sync
schedules are changed by this correction.

## Verification

`tests/test_chart_windows.py` covers every UI choice, leap dates, month/year
transitions, DST dates and multiple timezone environments, inclusive boundaries,
future exclusion, zero filling, exact API/page ranges, and browser label handling.
`tests/chart_preview.py` is a read-only in-memory fictional preview. Run it only
in an isolated source copy without `.env`, bound to localhost, and stop it after
QA. Its single SQLite connection is serialized for concurrent chart requests;
the production connection pool is unchanged.
