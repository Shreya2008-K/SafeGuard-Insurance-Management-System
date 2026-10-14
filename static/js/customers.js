/* Customers page: list / search / add / edit / delete via /api/customers */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const rows = $('rows'), form = $('customerForm');
  const params = new URLSearchParams(location.search);
  const state = { page: 1, q: params.get('q') || '' };
  $('q').value = state.q;

  async function load() {
    SG.loadingRow(rows, 10);
    try {
      const d = await SG.api('GET', `/api/customers?page=${state.page}&q=${encodeURIComponent(state.q)}`);
      if (d.page > d.pages) { state.page = d.pages; return load(); }
      $('resultInfo').textContent = state.q ? `${d.total} match${d.total === 1 ? '' : 'es'}` : '';
      if (!d.items.length) SG.emptyRow(rows, 10, state.q ? 'No customers match your search.' : 'No customers yet. Add your first customer to get started.');
      else rows.innerHTML = d.items.map((c) => `<tr>
        <td>${c.customer_id}</td><td><strong>${SG.esc(c.full_name)}</strong></td><td>${c.age ?? '—'}</td><td>${SG.esc(c.gender || '—')}</td>
        <td>${SG.esc(c.phone || '—')}</td><td>${SG.esc(c.email || '—')}</td><td class="wrap">${SG.esc(c.address || '—')}</td>
        <td>${SG.date(c.created_at)}</td>
        <td>${c.policy_count ? `<a class="id-link" href="/policies?customer_id=${c.customer_id}">${c.policy_count} polic${c.policy_count === 1 ? 'y' : 'ies'}</a>` : '0'}</td>
        <td class="actions">${SG.canWrite ? `<button class="row-btn" data-edit="${c.customer_id}">Edit</button><button class="row-btn danger" data-del="${c.customer_id}" data-name="${SG.esc(c.full_name)}">Delete</button>` : ''}</td></tr>`).join('');
      SG.renderPager($('pager'), d, (p) => { state.page = p; load(); });
    } catch (e) { SG.errorRow(rows, 10, e.message); }
  }

  function openForm(customer) {
    SG.clearErrors(form); form.reset(); SG.fillForm(form, customer || {});
    $('formTitle').textContent = customer ? `Edit customer #${customer.customer_id}` : 'Add Customer';
    SG.openModal('formModal');
  }
  if (!SG.canWrite) $('addBtn').hidden = true;
  $('addBtn').addEventListener('click', () => openForm(null));
  $('q').addEventListener('input', SG.debounce((e) => { state.q = e.target.value.trim(); state.page = 1; load(); }));

  rows.addEventListener('click', async (e) => {
    const edit = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]');
    try {
      if (edit) openForm(await SG.api('GET', '/api/customers/' + edit.dataset.edit));
      if (del && await SG.confirm({ title: 'Delete customer?', message: `${del.dataset.name} will be permanently removed. This cannot be undone.` })) {
        const r = await SG.api('DELETE', '/api/customers/' + del.dataset.del);
        SG.toast(r.message); load();
      }
    } catch (err) { SG.toast(err.message, 'error'); }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!SG.checkForm(form)) return;
    const data = SG.formData(form), id = data.customer_id, btn = $('saveBtn');
    btn.disabled = true;
    try {
      const r = await (id ? SG.api('PUT', '/api/customers/' + id, data) : SG.api('POST', '/api/customers', data));
      SG.closeModal('formModal'); SG.toast(r.message); load();
    } catch (err) {
      if (err.errors && Object.keys(err.errors).length) SG.setErrors(form, err.errors);
      SG.toast(err.message, 'error');
    } finally { btn.disabled = false; }
  });
  load();
})();
