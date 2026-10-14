/* Agents page: list / search / add / edit / change status / delete via /api/agents */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const rows = $('rows'), form = $('agentForm');
  const state = { page: 1, q: '', status: '' };

  async function load() {
    SG.loadingRow(rows, 8);
    try {
      const d = await SG.api('GET', `/api/agents?page=${state.page}&q=${encodeURIComponent(state.q)}&status=${state.status}`);
      if (d.page > d.pages) { state.page = d.pages; return load(); }
      if (!d.items.length) SG.emptyRow(rows, 8, state.q || state.status ? 'No agents match your filters.' : 'No agents yet. Add your first agent.');
      else rows.innerHTML = d.items.map((a) => `<tr>
        <td>${a.agent_id}</td><td><strong>${SG.esc(a.agent_name)}</strong></td><td>${SG.esc(a.phone || '—')}</td><td>${SG.esc(a.email || '—')}</td>
        <td class="num">${a.commission_percentage.toFixed(1)}%</td>
        <td><a class="id-link" href="/policies?agent_id=${a.agent_id}">${a.policy_count}</a></td><td>${SG.badge(a.status)}</td>
        <td class="actions">${!SG.canWrite ? '' : `<button class="row-btn" data-toggle="${a.agent_id}" data-status="${a.status}">${a.status === 'Active' ? 'Deactivate' : 'Activate'}</button>
          <button class="row-btn" data-edit="${a.agent_id}">Edit</button><button class="row-btn danger" data-del="${a.agent_id}" data-name="${SG.esc(a.agent_name)}">Delete</button>`}</td></tr>`).join('');
      SG.renderPager($('pager'), d, (p) => { state.page = p; load(); });
    } catch (e) { SG.errorRow(rows, 8, e.message); }
  }

  function openForm(agent) {
    SG.clearErrors(form); form.reset(); SG.fillForm(form, agent || { status: 'Active' });
    $('formTitle').textContent = agent ? `Edit agent #${agent.agent_id}` : 'Add Agent';
    SG.openModal('formModal');
  }
  if (!SG.canWrite) $('addBtn').hidden = true;
  $('addBtn').addEventListener('click', () => openForm(null));
  $('q').addEventListener('input', SG.debounce((e) => { state.q = e.target.value.trim(); state.page = 1; load(); }));
  $('statusFilter').addEventListener('change', (e) => { state.status = e.target.value; state.page = 1; load(); });

  rows.addEventListener('click', async (e) => {
    const edit = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]'), tog = e.target.closest('[data-toggle]');
    try {
      if (edit) openForm(await SG.api('GET', '/api/agents/' + edit.dataset.edit));
      if (tog) { const r = await SG.api('PATCH', `/api/agents/${tog.dataset.toggle}/status`, { status: tog.dataset.status === 'Active' ? 'Inactive' : 'Active' }); SG.toast(r.message); load(); }
      if (del && await SG.confirm({ title: 'Delete agent?', message: `${del.dataset.name} will be permanently removed. Agents with policies cannot be deleted.` })) {
        const r = await SG.api('DELETE', '/api/agents/' + del.dataset.del); SG.toast(r.message); load();
      }
    } catch (err) { SG.toast(err.message, 'error'); }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!SG.checkForm(form)) return;
    const data = SG.formData(form), id = data.agent_id, btn = $('saveBtn');
    btn.disabled = true;
    try {
      const r = await (id ? SG.api('PUT', '/api/agents/' + id, data) : SG.api('POST', '/api/agents', data));
      SG.closeModal('formModal'); SG.toast(r.message); load();
    } catch (err) {
      if (err.errors && Object.keys(err.errors).length) SG.setErrors(form, err.errors);
      SG.toast(err.message, 'error');
    } finally { btn.disabled = false; }
  });
  load();
})();
