# Local connection cleanup and recovery

The cleanup command archives one deliberately unlinked legacy Citi credit
account, disables Teller locally when no other active account needs it, and
archives obsolete E*TRADE authorization attempts created during a specified
repair window before the currently working authorization.

It preserves all financial rows, credentials, provider grants, settings and
older historical authorizations. It creates and verifies an owner-only backup
and a scoped recovery receipt in `data/backups/`. It makes no network requests.

Run only with explicit approval for the card and provider retirement, with
MyPocket stopped:

```sh
.venv/bin/python -m mypocket.local_cleanup retire --since YYYY-MM-DD
```

To undo only those archive/status changes, stop MyPocket and run:

```sh
.venv/bin/python -m mypocket.local_cleanup restore
```

Recovery verifies current state before changing it and preserves later changes
to unrelated records. It does not replace the live database with an old copy.
Do not publish backups or the recovery receipt.

Normal synchronization starts 30 seconds after app startup. Citi/Plaid checks
every 30 minutes after completion; E*TRADE keeps its six-hour interval. Plaid
failures back off up to six hours, resetting after success. The app must be
running and the Mac awake. Disabled Teller and
archived connection attempts are excluded. Active Plaid and E*TRADE connections
continue to sync; authorization expiry still requires the owner's action.
An excluded card's unpaid debt and purchases are not represented by a checking
balance, even if that checking account makes its card payments.
