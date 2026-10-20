/* Local-only Link and staged cutover. Provider text is rendered with textContent. */
(() => {
  const get = id => document.getElementById(id);
  if (!get('plaidApp')) return;
  let busy = false, review = null, plan = null, enrollmentId = null;
  let mappingInputs = [];
  const status = text => { get('plaidStatus').textContent = text; };
  const el = (tag, text, className = '') => {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  };
  async function request(action, body) {
    const response = await fetch('/api/plaid/' + action, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-MyPocket-Request': '1'},
      body: JSON.stringify(body),
    });
    let data;
    try { data = await response.json(); }
    catch { throw new Error('MyPocket returned an unexpected response. Refresh the page.'); }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed.');
    if (data.errors?.length) throw new Error(data.errors.join('; '));
    return data;
  }
  async function run(button, action) {
    if (busy) return;
    busy = true;
    button.disabled = true;
    try { await action(); }
    catch (error) { status(error.message || 'The request could not be completed.'); }
    finally { busy = false; button.disabled = false; }
  }
  function invalidatePlan() {
    plan = null;
    get('plaidPlan').hidden = true;
    get('plaidConfirm').checked = false;
    get('plaidConfirm').disabled = true;
    get('plaidApprove').disabled = true;
  }
  async function launch(button) {
    status('Preparing secure Link…');
    const id = Number(button.dataset.link) || null;
    const flow = await request('link_token', {enrollment_id: id});
    if (typeof Plaid === 'undefined') throw new Error('Plaid Link could not load. Check network access and content blockers.');
    const handler = Plaid.create({
      token: flow.link_token,
      onSuccess: async publicToken => {
        status('Saving connection. No accounts are imported yet…');
        try {
          if (flow.update) await request('reconnect_complete', {flow_id: flow.flow_id});
          else await request('exchange', {flow_id: flow.flow_id, public_token: publicToken});
          status('Connection saved. Review accounts before importing.');
          window.location.reload();
        } catch (error) { status(error.message || 'Could not save the connection. Do not create another Item.'); }
        finally { handler.destroy(); }
      },
      onExit: error => {
        status(error ? 'Link could not finish. Retry this connection; do not create a duplicate Item.' : 'Link closed. No account mapping changed.');
        handler.destroy();
      },
    });
    handler.open();
  }
  async function loadReview(button) {
    invalidatePlan();
    status('Fetching account and transaction history for review…');
    enrollmentId = Number(button.dataset.review);
    review = await request('review', {enrollment_id: enrollmentId});
    mappingInputs = [];
    get('plaidMappings').replaceChildren();
    for (const account of review.accounts) {
      const group = el('div', '', 'mb-4');
      const label = el('label', `${account.name} · ${account.type} · ending ${account.mask || 'unknown'}`, 'block text-sm mb-2');
      const select = el('select', '', 'w-full bg-zinc-950 border border-zinc-700 rounded-lg p-3 text-sm');
      select.id = 'plaid-account-' + mappingInputs.length;
      label.htmlFor = select.id;
      const option = (value, text) => { const o = el('option', text); o.value = value; select.append(o); };
      option('', 'Choose what this account matches…');
      for (const existing of review.existing_accounts.filter(x => x.type === account.type)) {
        option('existing:' + existing.id, `Existing: ${existing.name} · history through ${existing.last_date || 'none'}`);
        option('replace:' + existing.id, `Replace history: ${existing.name} · archive old records and rebuild from Plaid`);
      }
      option('new', 'Genuinely new account — absent from MyPocket');
      option('ignore', 'Ignore — keep this account out of MyPocket');
      select.value = '';
      select.addEventListener('change', invalidatePlan);
      group.append(label, select);
      get('plaidMappings').append(group);
      mappingInputs.push({account, select});
    }
    get('plaidReview').hidden = false;
    status('Choose matches, then preview. No balances or transactions have changed.');
  }
  async function preview() {
    if (!review || mappingInputs.some(x => !x.select.value)) throw new Error('Choose an action for every account first.');
    const selections = mappingInputs.map(({account, select}) => {
      const [mode, id] = select.value.split(':');
      return {external_id: account.external_id, mode, account_id: id ? Number(id) : null};
    });
    invalidatePlan();
    status('Preparing the cutover preview…');
    plan = await request('preview', {enrollment_id: enrollmentId, review_id: review.review_id, selections});
    get('plaidWarning').textContent = plan.warning;
    get('plaidPlanRows').replaceChildren();
    for (const account of plan.accounts) {
      let text = account.mode === 'ignore' ? `${account.name}: ignored.` :
        `${account.name}: ${account.mode === 'replace' ? 'archive old account and rebuild from Plaid' : account.mode === 'new' ? 'create a new account' : 'reuse existing account'}; ` +
        `${account.preserved} existing transactions retained; ${account.history_skipped} overlapping records skipped; ` +
        `${account.transactions_to_import} records to import (${account.pending} pending in received history). ` +
        `Import dates: ${account.import_from ? account.import_from + ' onward' : 'all available history'}. ` +
        `Cached balance: ${account.balance === null ? 'unavailable; existing balance retained' : account.balance + ' USD'}.`;
      if (account.mode === 'replace') text += ` ${account.archived} old transactions, including ${account.legacy_pending} legacy pending entries, will be archived and excluded from active totals. ` +
        `Available Plaid history: ${account.received_from} through ${account.received_through}; earlier history may be unavailable. A private backup and scoped restoration are retained.`;
      else if (account.legacy_pending) text += ` Blocked: ${account.legacy_pending} unresolved legacy pending transactions ` +
        `from ${account.legacy_pending_from} through ${account.legacy_pending_through}. Review these existing entries before cutover.`;
      get('plaidPlanRows').append(el('p', text, 'mb-3'));
    }
    get('plaidPlan').hidden = false;
    get('plaidConfirm').disabled = !!plan.blocked || !plan.plan_id;
    status(plan.blocked ? 'Preview blocked by unresolved legacy pending transactions. No balances, history, or account mappings changed.' :
      'Preview ready. Check the history boundary and account matches before confirming.');
  }
  async function approve() {
    if (!plan || plan.blocked || !plan.plan_id || !get('plaidConfirm').checked) throw new Error('Resolve any preview blockers, then review and confirm the mapping.');
    if (get('plaidApp').dataset.environment !== 'production') throw new Error('Sandbox is preview-only. No data can be imported.');
    status('Applying your reviewed mapping…');
    await request('approve', {enrollment_id: enrollmentId, plan_id: plan.plan_id, confirmed: true});
    status('Mapping applied. Selected replacements archived; Plaid sync enabled.');
    window.location.reload();
  }
  document.querySelectorAll('[data-link]').forEach(button => button.addEventListener('click', () => run(button, () => launch(button))));
  document.querySelectorAll('[data-review]').forEach(button => button.addEventListener('click', () => run(button, () => loadReview(button))));
  document.querySelectorAll('[data-sync]').forEach(button => button.addEventListener('click', () => run(button, async () => {
    status('Syncing cached balances and transaction updates…');
    const result = await request('sync', {enrollment_id: Number(button.dataset.sync)});
    status(`Sync complete: ${result.transactions_created || 0} new transactions.`);
  })));
  get('plaidPreview').addEventListener('click', () => run(get('plaidPreview'), preview));
  get('plaidApprove').addEventListener('click', async () => {
    await run(get('plaidApprove'), approve);
    get('plaidApprove').disabled = !plan || plan.blocked || !plan.plan_id || !get('plaidConfirm').checked;
  });
  get('plaidConfirm').addEventListener('change', () => {
    get('plaidApprove').disabled = busy || !plan || plan.blocked || !plan.plan_id || !get('plaidConfirm').checked;
  });
})();
