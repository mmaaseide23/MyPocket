# Citi with Plaid Trial

This adapter is optional. Teller and E*TRADE remain available. It requests only
Transactions and reads cached balances from `/accounts/get`; it does not call
Auth, Identity, Investments, Refresh, transfers, or realtime Balance endpoints.

## Your setup steps

1. In your existing [Plaid Dashboard](https://dashboard.plaid.com/), confirm your
   application is approved for **Trial with production access**. If you only see
   Sandbox, complete the Trial application yourself. You must personally handle
   identity verification, the MSA and security attestations. Do not choose a paid
   upgrade or enter payment details. Eligibility is determined by Plaid.
2. In Dashboard's developer API-key area, personally obtain your client ID and
   **production** secret. Open MyPocket's local `.env` in your editor and set:

   ```dotenv
   PLAID_CLIENT_ID=<your client ID>
   PLAID_SECRET=<your production secret>
   PLAID_ENVIRONMENT=production
   PLAID_TRIAL_CONFIRMED=true
   ```

   Replace placeholders locally. Keep `.env` private and out of Git. Never send
   keys, bank passwords or access tokens in chat. Do not rotate or create keys
   just for troubleshooting without understanding the effect on existing apps.
   Trial uses production endpoints; `trial` is not an API environment.
3. After the tested code is installed, coordinate one MyPocket restart. Loading
   settings requires a restart; existing open authorization links should be
   finished or closed first. A restart briefly interrupts local requests.
4. Open `http://localhost:8000/plaid` in a desktop browser, unlock MyPocket,
   and select **Open Plaid Link**. Allow its popup, choose **Citi**, and personally
   complete the bank login and access approval. No public callback URL or public
   deployment is needed for this desktop popup flow. Embedded mobile browsers
   are not supported by this integration.
5. Select **Review accounts**. If history is still loading, wait a few minutes
   and Review again; do not create another Item. Match each remote account to
   its existing MyPocket account. Use New only for an account that is genuinely
   absent. Ignore accounts you do not want imported.
6. Select **Preview changes**. Review each account, proposed cached balance,
   import count and history boundary. Only after you approve the plan should
   you check the confirmation and select **Apply reviewed mapping**.

Trial currently supports Citi OAuth, Transactions and Balance, with no payment
setup. It permits **10 lifetime-created Items**; duplicate links and removed
Items still count. MyPocket limits initial linking to one Citi Item per
configured environment and uses update mode for reconnects. It cannot verify
billing-plan eligibility or guarantee the provider's future terms. Stop if the
Dashboard requires a paid plan for your account.

Official references, checked October 20, 2026:

- [Trial eligibility, Citi OAuth and limits](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan)
- [Trial does not require invoices or payment setup](https://support.plaid.com/hc/en-us/articles/16195021021207-How-do-I-pay-my-Plaid-invoice)
- [Desktop OAuth popup behavior](https://plaid.com/docs/link/oauth/)
- [Link setup and update mode](https://plaid.com/docs/api/link/)
- [Transaction sync and pagination](https://plaid.com/docs/transactions/sync-migration/)

## Reconciliation limits

Linking only saves an encrypted pending connection. It does not automatically
create local accounts, alter mappings, or import financial data. Preview data is
encrypted using the app's existing encryption mechanism; no account payloads or
provider errors are written to application logs.

For an existing account, import starts on the day **after its latest stored
transaction date**. All earlier local history, categories and notes remain;
Plaid's overlapping earlier dates are skipped permanently. This intentionally
does not claim to reconcile missing transactions on an incomplete historical
day. Review that boundary carefully. Pending transactions in legacy history
block cutover until a separate manual reconciliation plan is approved.

Only the mapped Teller accounts stop receiving Teller writes. Their source IDs
remain in place so Teller cannot create duplicate accounts. Other Teller
accounts and all E*TRADE accounts remain independent. No provider enrollment is
removed or revoked. A generic Delete cannot remove a Plaid-owned account or its
connection; an explicit ownership/reconciliation change is needed first.

Subsequent Plaid sync is atomic per Item, including balance updates and the
cursor. It fetches all pages, restarts pagination on mutation, deduplicates by
provider transaction ID, and handles modified/removed records. Pending and
removed Plaid transactions remain in the database with their annotations but
are excluded from spending and transaction views. A posted transaction reuses
its pending row where Plaid supplies `pending_transaction_id`.

If account selection changes after approval, sync pauses rather than importing
new accounts automatically. A new reconciliation plan is then required; this
Existing approved mappings remain locked when adding accounts to an Item. It supports
USD checking, savings and credit cards. Sandbox is preview-only through the UI
and cannot import simulated data into the real dashboard.

The existing single-process scheduler checks Plaid 30 seconds after startup,
then 30 minutes after each completed run. Failures back off to 1, 2, 4, then
at most 6 hours; success restores the 30-minute interval. E*TRADE stays on its
six-hour interval. Runs are sequential; existing bank locks also serialize
manual sync/import with Plaid. No new service or public webhook is installed.
The Mac must be awake and the app running; an open browser is not required.

Only cached `/accounts/get` and incremental `/transactions/sync` are used.
No Transactions Refresh add-on, paid upgrade, extra Item or new permission is
requested. The existing free Trial remains subject to its plan limits. More
frequent checks only import updates already available from Plaid; Citi/Plaid
control when fresh bank data becomes available. Plaid typically checks the bank
1–4 times daily. See the [official sync documentation](https://plaid.com/docs/api/products/transactions/#transactionssync)
and [Trial billing documentation](https://plaid.com/docs/account/billing/#trial-plans).

Cadence and sync behavior are verified with virtual clocks and synthetic
fixtures, without inspecting real financial data or executing a live test sync.
