# Recoverable Citi replacement

Choose **Replace history** only for an explicitly authorized account reset.
Approval creates and verifies a private SQLite backup, including a restoration
test, before changing any accounts. It then archives the selected old account and
imports all currently available Plaid history into a new active account in one
database transaction. Old rows, notes and categories remain with the archived
account; they are excluded from active dashboards. No provider credentials or
bank accounts are changed. Unlinked accounts remain unchanged.

Provider history is limited to what the existing Link returns. The preview and
connection page show the actual coverage. Pending Plaid transactions are retained
but excluded from totals. No additional Item is created.

## Restore the old checking view

Stop the local MyPocket service first to avoid an in-flight sync. From this repo:

```sh
.venv/bin/python -m mypocket.citi_reset restore
```

This requires exactly one active replacement with an unchanged account mapping.
It verifies another backup, restores the old account to active views, archives
the replacement account (retaining its fresh records), and pauses the associated
Plaid enrollment locally. It does not revoke access or delete any transactions.
Other accounts, E*TRADE, settings and credentials remain untouched. Restart
MyPocket afterward. Plaid stays paused until a separately reviewed resumption.

The command is idempotent after successful restoration. If scope checks fail,
it rolls back without changes. It never overwrites the entire live database with
an old copy. Backups stay in `data/backups/`, readable only by the Mac account
owner. The `citireset` record identifies the original and replacement locally.
Do not publish database backups or paste their contents into chat.
