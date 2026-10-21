# Add a Citi credit card to the existing connection

1. Open MyPocket → Connections → Citi via Plaid → **Add Citi accounts**.
2. Complete Citi login and consent yourself. Keep checking selected and select the card too. Do not create a second Item or upgrade the plan.
3. Back in MyPocket, choose **Review accounts**. If history is still preparing, wait and review again; do not relink.
4. Checking is locked to **Keep existing Plaid mapping**. For a card whose old copy is already archived, choose **New active account — retain any archived copy**. The old archive remains excluded from balances and spending.
5. Choose **Preview changes**, check the card, counts and available dates, then personally confirm and apply the reviewed mapping.

Adding accounts uses [Plaid update mode](https://plaid.com/docs/link/update-mode/#using-update-mode-to-request-new-accounts) with the existing Item. It does not exchange another public token or request additional products. The local Trial confirmation cannot determine the Dashboard's current plan or remaining lifetime Item quota; check the Dashboard if uncertain. No paid refresh call is used.

Accounts newly authorized at Citi remain outside MyPocket totals until import approval. Existing mapped accounts can continue syncing. De-selecting an existing account at Citi instead pauses sync with an explicit error; no local records are deleted.

An addition requires a verified, restorable SQLite backup under `data/backups/` before changing financial records. It preserves existing mapped account IDs, balances, transactions, notes, categories and the incremental cursor during approval. Subsequent normal sync applies any outstanding updates. The new account uses only the history Plaid actually returns; earlier dates may be missing. Pending transactions stay excluded.

Card balances are debt: positive amounts owed are subtracted from net worth. Card purchases count as spending. Explicit card payments and Plaid's exact credit-card-payment category are imported as Transfers; they are not purchases, income or refunds. Existing transaction categories and notes are not rewritten. Ambiguous payment descriptions and existing incorrectly categorized payments need user review.

For recovery, stop MyPocket and retain both the pre-addition backup and the current database before any restoration. A full backup restores all local state to that point and would also roll back subsequent checking/E*TRADE updates; do not perform it without reviewing that scope. Existing checking-reset and cleanup recovery commands intentionally refuse if their original account scope has changed. Do not bypass those guards or delete a working Item.
