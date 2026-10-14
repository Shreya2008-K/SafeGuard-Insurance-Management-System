/* User Management (admin only). Every action is enforced again by the server (HTTP 403 for non-admins). */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id), rows = $('rows');
  const form = $('userForm'), roleForm = $('roleForm'), pwForm = $('pwForm');
  let users = [], me = null, target = null;
  const ROLE = { admin: 'Admin', manager: 'Manager', staff: 'Staff' };
  const roleBadge = (r) => `<span class="badge badge-role-${r}">${ROLE[r]}</span>`;

  async function load() {
    SG.loadingRow(rows, 8);
    try {
      const d = await SG.api('GET', '/api/users'); users = d.items; me = d.me;
      rows.innerHTML = users.map((u) => `<tr>
        <td>${u.user_id}</td><td><strong>${SG.esc(u.username)}</strong>${u.user_id === me ? ' <span class="muted">(you)</span>' : ''}</td>
        <td>${SG.esc(u.display_name || '—')}</td><td>${SG.esc(u.email || '—')}</td><td>${roleBadge(u.role)}</td>
        <td>${SG.badge(u.is_active ? 'Active' : 'Inactive')}</td><td>${SG.date(u.created_at)}</td>
        <td class="actions"><button class="row-btn" data-act="role" data-id="${u.user_id}">Role</button>
          <button class="row-btn" data-act="active" data-id="${u.user_id}">${u.is_active ? 'Disable' : 'Enable'}</button>
          <button class="row-btn" data-act="password" data-id="${u.user_id}">Reset password</button>
          <button class="row-btn danger" data-act="delete" data-id="${u.user_id}">Delete</button></td></tr>`).join('');
    } catch (e) { SG.errorRow(rows, 8, e.message); }
  }

  $('addBtn').addEventListener('click', () => { SG.clearErrors(form); form.reset(); form.role.value = 'staff'; SG.openModal('formModal'); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!SG.checkForm(form)) return;
    const d = SG.formData(form);
    if (d.password !== d.confirm_password) { SG.setErrors(form, { confirm_password: 'Passwords do not match.' }); return; }
    const btn = $('saveBtn'); btn.disabled = true;
    try { const r = await SG.api('POST', '/api/users', d); SG.closeModal('formModal'); SG.toast(r.message); load(); }
    catch (err) { if (err.errors && Object.keys(err.errors).length) SG.setErrors(form, err.errors); SG.toast(err.message, 'error'); }
    finally { btn.disabled = false; }
  });

  rows.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    target = users.find((u) => String(u.user_id) === b.dataset.id);
    try {
      if (b.dataset.act === 'role') { $('roleTitle').textContent = `Change role for ${target.username}`; $('newRole').value = target.role; SG.openModal('roleModal'); }
      if (b.dataset.act === 'password') { SG.clearErrors(pwForm); pwForm.reset(); $('pwTitle').textContent = `Reset password for ${target.username}`; SG.openModal('pwModal'); }
      if (b.dataset.act === 'active') {
        const enable = !target.is_active;
        if (enable || await SG.confirm({ title: `Disable ${target.username}?`, message: 'They will be signed out immediately and cannot log in until re-enabled.', confirmLabel: 'Disable' })) {
          const r = await SG.api('PATCH', `/api/users/${target.user_id}/active`, { is_active: enable }); SG.toast(r.message); load();
        }
      }
      if (b.dataset.act === 'delete' && await SG.confirm({ title: `Delete ${target.username}?`, message: 'The account is permanently removed. This cannot be undone.' })) {
        const r = await SG.api('DELETE', '/api/users/' + target.user_id); SG.toast(r.message); load();
      }
    } catch (err) { SG.toast(err.message, 'error'); }
  });

  roleForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    try { const r = await SG.api('PATCH', `/api/users/${target.user_id}/role`, { role: $('newRole').value }); SG.closeModal('roleModal'); SG.toast(r.message); load(); }
    catch (err) { SG.toast(err.message, 'error'); }
  });
  pwForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!SG.checkForm(pwForm)) return;
    const d = SG.formData(pwForm);
    if (d.new_password !== d.confirm_password) { SG.setErrors(pwForm, { confirm_password: 'Passwords do not match.' }); return; }
    try { const r = await SG.api('POST', `/api/users/${target.user_id}/password`, d); SG.closeModal('pwModal'); SG.toast(r.message); }
    catch (err) { if (err.errors && Object.keys(err.errors).length) SG.setErrors(pwForm, err.errors); SG.toast(err.message, 'error'); }
  });
  load();
})();
