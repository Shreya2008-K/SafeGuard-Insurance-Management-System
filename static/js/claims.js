/* Claims page: submit, search, filter, review, approve / reject via /api/claims */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const rows = $('rows'), form = $('claimForm');
  const params = new URLSearchParams(location.search);
  const state = { page: 1, claimId: '', policyId: params.get('policy_id') || '', status: params.get('status') || '' };
  $('policyId').value = state.policyId; $('statusFilter').value = state.status;

  async function load() {
    SG.loadingRow(rows, 8);
    const qs = new URLSearchParams({ page: state.page, claim_id: state.claimId, policy_id: state.policyId, status: state.status });
    try {
      const d = await SG.api('GET', '/api/claims?' + qs);
      if (d.page > d.pages) { state.page = d.pages; return load(); }
      SG.summaryStrip($('summary'), [{ label: 'Claims (matching filters)', value: SG.num(d.summary.count) }, { label: 'Pending review', value: SG.num(d.summary.pending_count) }, { label: 'Approved total', value: SG.money(d.summary.approved_total) }]);
      if (!d.items.length) SG.emptyRow(rows, 8, 'No claims match your filters.');
      else rows.innerHTML = d.items.map((c) => `<tr>
        <td><strong>${SG.cid(c.claim_id)}</strong></td><td>${SG.pid(c.policy_id)}</td><td>${SG.esc(c.customer_name)}</td><td>${SG.date(c.claim_date)}</td>
        <td class="num">${SG.money(c.claim_amount)}</td><td>${SG.badge(c.claim_status)}</td><td>${SG.date(c.decision_date)}</td>
        <td class="actions"><button class="row-btn" data-view="${c.claim_id}">${c.claim_status === 'Pending' && SG.canWrite ? 'Review' : 'View'}</button></td></tr>`).join('');
      SG.renderPager($('pager'), d, (p) => { state.page = p; load(); });
    } catch (e) { SG.errorRow(rows, 8, e.message); }
  }

  const refilter = () => { state.page = 1; load(); };
  $('claimId').addEventListener('input', SG.debounce((e) => { state.claimId = e.target.value.trim(); refilter(); }));
  $('policyId').addEventListener('input', SG.debounce((e) => { state.policyId = e.target.value.trim(); refilter(); }));
  $('statusFilter').addEventListener('change', (e) => { state.status = e.target.value; refilter(); });

  /* ---- submit claim ---- */
  async function openForm(policyId) {
    SG.clearErrors(form); form.reset();
    try {
      const l = await SG.api('GET', '/api/lookups');
      $('policy_id').innerHTML = '<option value="">Select policy…</option>' + l.active_policies.map((p) => `<option value="${p.policy_id}">${SG.pid(p.policy_id)} — ${SG.esc(p.customer_name)} — ${SG.esc(p.policy_type)} (cover ${SG.money(p.coverage_amount)})</option>`).join('');
      if (policyId) $('policy_id').value = policyId;
      $('claim_date').value = SG.today();
      SG.openModal('formModal');
    } catch (e) { SG.toast(e.message, 'error'); }
  }
  if (!SG.canWrite) $('addBtn').hidden = true;
  $('addBtn').addEventListener('click', () => openForm(state.policyId));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!SG.checkForm(form)) return;
    const btn = $('saveBtn'); btn.disabled = true;
    try {
      const r = await SG.api('POST', '/api/claims', SG.formData(form));
      SG.closeModal('formModal'); SG.toast(r.message); load(); SG.refreshNotifications();
    } catch (err) {
      if (err.errors && Object.keys(err.errors).length) SG.setErrors(form, err.errors);
      SG.toast(err.message, 'error');
    } finally { btn.disabled = false; }
  });

  /* ---- review / decide ---- */
  async function openDetail(id) {
    try {
      const c = await SG.api('GET', '/api/claims/' + id);
      $('detailTitle').textContent = `Claim ${SG.cid(c.claim_id)}`;
      $('detailBody').innerHTML = `<div class="detail-grid">
        <div><span>Status</span>${SG.badge(c.claim_status)}</div><div><span>Customer</span><strong>${SG.esc(c.customer_name)}</strong></div>
        <div><span>Policy</span><strong>${SG.pid(c.policy_id)} · ${SG.esc(c.policy_type)}</strong></div><div><span>Policy coverage</span><strong>${SG.money(c.coverage_amount)}</strong></div>
        <div><span>Claim date</span><strong>${SG.date(c.claim_date)}</strong></div><div><span>Claim amount</span><strong>${SG.money(c.claim_amount)}</strong></div>
        <div><span>Decision date</span><strong>${SG.date(c.decision_date)}</strong></div><div><span>Decision note</span><strong>${SG.esc(c.decision_note || '—')}</strong></div>
        <div style="grid-column:span 2"><span>Reason</span><strong>${SG.esc(c.claim_reason || '—')}</strong></div></div>
        <h4 style="margin:8px 0">Claim history for this policy</h4>
        <div class="table-wrap"><table class="table"><thead><tr><th>Claim</th><th>Date</th><th class="num">Amount</th><th>Status</th></tr></thead><tbody>
        ${c.history.map((h) => `<tr><td>${SG.cid(h.claim_id)}</td><td>${SG.date(h.claim_date)}</td><td class="num">${SG.money(h.claim_amount)}</td><td>${SG.badge(h.claim_status)}</td></tr>`).join('')}</tbody></table></div>
        ${c.claim_status === 'Pending' && SG.canWrite ? '<div class="field" style="margin-top:16px"><label for="note">Decision note <span class="opt">(optional)</span></label><input id="note" maxlength="300"></div>' : ''}`;
      $('detailFoot').innerHTML = c.claim_status === 'Pending' && SG.canWrite
        ? '<button class="btn btn-outline" data-close>Close</button><button class="btn btn-danger" data-decide="reject">Reject claim</button><button class="btn btn-success" data-decide="approve">Approve claim</button>'
        : '<button class="btn btn-outline" data-close>Close</button>';
      $('detailFoot').dataset.id = id;
      SG.openModal('detailModal');
    } catch (e) { SG.toast(e.message, 'error'); }
  }
  rows.addEventListener('click', (e) => { const b = e.target.closest('[data-view]'); if (b) openDetail(b.dataset.view); });
  $('detailFoot').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-decide]');
    if (!b) return;
    const action = b.dataset.decide, id = $('detailFoot').dataset.id;
    const ok = await SG.confirm({ title: `${action === 'approve' ? 'Approve' : 'Reject'} claim ${SG.cid(id)}?`, message: 'The decision is final: only pending claims can be decided.', confirmLabel: action === 'approve' ? 'Approve' : 'Reject' });
    if (!ok) return;
    try {
      const r = await SG.api('POST', `/api/claims/${id}/${action}`, { note: ($('note') || {}).value || '' });
      SG.closeModal('detailModal'); SG.toast(r.message); load(); SG.refreshNotifications();
    } catch (err) { SG.toast(err.message, 'error'); }
  });

  load();
  if (params.get('new') === '1' && SG.canWrite) openForm(state.policyId);
})();
