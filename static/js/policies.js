/* Policies page: list / filter / create / edit via /api/policies, premium calculator */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const rows = $('rows'), form = $('policyForm');
  const params = new URLSearchParams(location.search);
  const state = { page: 1, q: params.get('q') || '', type: '', status: '', customerId: params.get('customer_id') || '', agentId: params.get('agent_id') || '' };
  let lookups = { customers: [], agents: [] };
  $('q').value = state.q;

  async function load() {
    SG.loadingRow(rows, 10);
    const qs = new URLSearchParams({ page: state.page, q: state.q, policy_type: state.type, status: state.status, customer_id: state.customerId, agent_id: state.agentId });
    try {
      const d = await SG.api('GET', '/api/policies?' + qs);
      if (d.page > d.pages) { state.page = d.pages; return load(); }
      SG.summaryStrip($('summary'), [{ label: 'Policies (matching filters)', value: SG.num(d.summary.count) }, { label: 'Total premium', value: SG.money(d.summary.total_premium) }, { label: 'Total coverage', value: SG.money(d.summary.total_coverage) }]);
      if (!d.items.length) SG.emptyRow(rows, 10, 'No policies match your filters.');
      else rows.innerHTML = d.items.map((p) => `<tr>
        <td><strong>${SG.pid(p.policy_id)}</strong></td><td>${SG.esc(p.customer_name)}</td><td>${SG.esc(p.agent_name || '—')}</td><td>${SG.esc(p.policy_type)}</td>
        <td class="num">${SG.money(p.premium_amount)}</td><td class="num">${SG.money(p.coverage_amount)}</td><td>${SG.date(p.start_date)}</td><td>${SG.date(p.end_date)}</td>
        <td>${SG.badge(p.status)}</td><td class="actions">${SG.canWrite ? `<button class="row-btn" data-edit="${p.policy_id}">Edit</button>` : ''}</td></tr>`).join('');
      SG.renderPager($('pager'), d, (p) => { state.page = p; load(); });
    } catch (e) { SG.errorRow(rows, 10, e.message); }
  }

  function renderChip() {
    const chip = $('customerChip');
    const parts = [];
    if (state.customerId) { const c = lookups.customers.find((x) => String(x.customer_id) === state.customerId); parts.push(`<span class="chip">Customer: ${SG.esc(c ? c.full_name : '#' + state.customerId)} <button data-clear="customerId" aria-label="Clear customer filter">&times;</button></span>`); }
    if (state.agentId) { const a = lookups.agents.find((x) => String(x.agent_id) === state.agentId); parts.push(`<span class="chip">Agent: ${SG.esc(a ? a.agent_name : '#' + state.agentId)} <button data-clear="agentId" aria-label="Clear agent filter">&times;</button></span>`); }
    chip.hidden = !parts.length; chip.style.background = 'none'; chip.style.padding = '0'; chip.innerHTML = parts.join(' ');
  }
  $('customerChip').addEventListener('click', (e) => { const b = e.target.closest('[data-clear]'); if (b) { state[b.dataset.clear] = ''; renderChip(); state.page = 1; load(); } });

  function fillSelect(sel, items, valueKey, labelFn, blank) {
    sel.innerHTML = (blank ? `<option value="">${blank}</option>` : '') + items.map((i) => `<option value="${i[valueKey]}">${SG.esc(labelFn(i))}</option>`).join('');
  }
  async function loadLookups() {
    lookups = await SG.api('GET', '/api/lookups');
    fillSelect($('customer_id'), lookups.customers, 'customer_id', (c) => `${c.full_name} (#${c.customer_id})`, 'Select customer…');
    fillSelect($('agent_id'), lookups.agents, 'agent_id', (a) => a.agent_name, 'No agent');
    renderChip();
  }

  function openForm(policy) {
    SG.clearErrors(form); form.reset(); $('calcResult').textContent = '';
    if (policy) {
      // an inactive agent already on this policy must stay selectable
      if (policy.agent_id && !lookups.agents.some((a) => a.agent_id === policy.agent_id)) $('agent_id').insertAdjacentHTML('beforeend', `<option value="${policy.agent_id}">${SG.esc(policy.agent_name)} (inactive)</option>`);
      SG.fillForm(form, { ...policy, status: policy.status === 'Cancelled' ? 'Cancelled' : 'Auto' });
    } else { SG.fillForm(form, { status: 'Auto', start_date: SG.today() }); }
    $('formTitle').textContent = policy ? `Edit policy ${SG.pid(policy.policy_id)}` : 'Create Policy';
    SG.openModal('formModal');
  }
  if (!SG.canWrite) $('addBtn').hidden = true;
  $('addBtn').addEventListener('click', () => openForm(null));
  rows.addEventListener('click', async (e) => {
    const edit = e.target.closest('[data-edit]');
    if (!edit) return;
    try { openForm(await SG.api('GET', '/api/policies/' + edit.dataset.edit)); } catch (err) { SG.toast(err.message, 'error'); }
  });

  $('q').addEventListener('input', SG.debounce((e) => { state.q = e.target.value.trim(); state.page = 1; load(); }));
  $('typeFilter').addEventListener('change', (e) => { state.type = e.target.value; state.page = 1; load(); });
  $('statusFilter').addEventListener('change', (e) => { state.status = e.target.value; state.page = 1; load(); });

  $('calcBtn').addEventListener('click', async () => {
    const d = SG.formData(form), cust = lookups.customers.find((c) => String(c.customer_id) === d.customer_id);
    const qs = new URLSearchParams({ policy_type: d.policy_type, coverage_amount: d.coverage_amount, age: cust && cust.age ? cust.age : '' });
    try {
      const r = await SG.api('GET', '/api/premium-estimate?' + qs);
      form.premium_amount.value = r.premium;
      $('calcResult').textContent = `Estimated ${SG.money(r.premium)} per year (base rate ${(r.base_rate * 100).toFixed(1)}%, age factor ${r.age_factor}). Premium field updated.`;
    } catch (err) { $('calcResult').textContent = ''; SG.toast(err.errors && err.errors.policy_type ? err.errors.policy_type : err.message, 'error'); }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!SG.checkForm(form)) return;
    const data = SG.formData(form), id = data.policy_id, btn = $('saveBtn');
    if (data.start_date && data.end_date && data.end_date <= data.start_date) { SG.setErrors(form, { end_date: 'End date must be after the start date.' }); return; }
    btn.disabled = true;
    try {
      const r = await (id ? SG.api('PUT', '/api/policies/' + id, data) : SG.api('POST', '/api/policies', data));
      SG.closeModal('formModal'); SG.toast(r.message); load(); SG.refreshNotifications();
    } catch (err) {
      if (err.errors && Object.keys(err.errors).length) SG.setErrors(form, err.errors);
      SG.toast(err.message, 'error');
    } finally { btn.disabled = false; }
  });

  loadLookups().then(() => { load(); if (params.get('new') === '1' && SG.canWrite) openForm(null); }).catch((e) => { SG.toast(e.message, 'error'); load(); });
})();
