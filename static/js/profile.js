/* Profile page. The server decides whose profile this is (the signed-in session); no user id is sent. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id), pForm = $('profileForm'), wForm = $('passwordForm');
  const ROLE = { admin: 'Admin', manager: 'Manager', staff: 'Staff' };

  function show(u) {
    $('accountInfo').innerHTML = `<dt>Username</dt><dd>${SG.esc(u.username)}</dd><dt>Role</dt><dd>${SG.esc(ROLE[u.role] || u.role)}</dd><dt>Member since</dt><dd>${SG.date(u.created_at)}</dd>`;
    SG.fillForm(pForm, u);
    const name = u.display_name || (u.role === 'admin' ? 'Administrator' : u.username);
    $('headerName').textContent = name;
    $('headerAvatar').textContent = (name.split(/[\s._-]+/).filter(Boolean).map((w) => w[0]).join('').slice(0, 2) || 'U').toUpperCase();
  }
  SG.api('GET', '/api/profile').then(show).catch((e) => SG.toast(e.message, 'error'));

  pForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!SG.checkForm(pForm)) return;
    const btn = $('profileBtn'); btn.disabled = true;
    try { const r = await SG.api('PUT', '/api/profile', SG.formData(pForm)); show(r.item); SG.toast(r.message); }
    catch (err) { if (err.errors && Object.keys(err.errors).length) SG.setErrors(pForm, err.errors); SG.toast(err.message, 'error'); }
    finally { btn.disabled = false; }
  });

  wForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!SG.checkForm(wForm)) return;
    const d = SG.formData(wForm);
    if (d.new_password !== d.confirm_password) { SG.setErrors(wForm, { confirm_password: 'Passwords do not match.' }); return; }
    const btn = $('passwordBtn'); btn.disabled = true;
    try { const r = await SG.api('POST', '/api/profile/password', d); wForm.reset(); SG.toast(r.message); }
    catch (err) { if (err.errors && Object.keys(err.errors).length) SG.setErrors(wForm, err.errors); SG.toast(err.message, 'error'); }
    finally { btn.disabled = false; }
  });
})();
