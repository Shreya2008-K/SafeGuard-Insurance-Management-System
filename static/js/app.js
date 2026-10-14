/* SafeGuard IMS - shared front-end helpers (loaded on every page after login). */
(function () {
  'use strict';
  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content;
  const SG = (window.SG = {});
  const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });

  SG.role = document.body.dataset.role || '';
  SG.canWrite = SG.role === 'admin' || SG.role === 'manager';  // UI hint only; the server enforces permissions
  SG.money = (v) => inr.format(Number(v || 0));
  SG.num = (v) => new Intl.NumberFormat('en-IN').format(Number(v || 0));
  SG.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  SG.date = (s) => {
    if (!s) return '—';
    const d = new Date(String(s).slice(0, 10) + 'T00:00:00');
    return isNaN(d) ? s : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  };
  SG.badge = (status) => `<span class="badge badge-${SG.esc(String(status).toLowerCase())}">${SG.esc(status)}</span>`;
  SG.pid = (id) => 'SG-' + String(id).padStart(4, '0');
  SG.cid = (id) => 'CLM-' + String(id).padStart(4, '0');
  SG.debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  SG.today = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };

  /* ---- API wrapper: always sends the CSRF token; rejects with {message, errors} ---- */
  SG.api = async function (method, url, body) {
    const opts = { method, headers: { Accept: 'application/json', 'X-CSRF-Token': csrf } };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, opts); } catch (e) { throw { message: 'Network error. Check your connection and try again.', errors: {} }; }
    if (res.status === 401) { window.location = '/login'; throw { message: 'Session expired.', errors: {} }; }
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (!res.ok) throw { message: data.error || 'Something went wrong. Please try again.', errors: data.errors || {}, status: res.status };
    return data;
  };

  /* ---- toasts ---- */
  SG.toast = function (message, type) {
    const el = document.createElement('div');
    el.className = 'toast' + (type === 'error' ? ' error' : '');
    el.textContent = message;
    document.getElementById('toasts').appendChild(el);
    setTimeout(() => el.remove(), type === 'error' ? 6000 : 3500);
  };

  /* ---- modals ---- */
  SG.openModal = (id) => { const m = document.getElementById(id); m.hidden = false; const f = m.querySelector('input:not([type=hidden]),select,textarea'); if (f) setTimeout(() => f.focus(), 30); };
  SG.closeModal = (id) => { document.getElementById(id).hidden = true; };
  document.addEventListener('click', (e) => {
    const closer = e.target.closest('[data-close]');
    if (closer) { const m = closer.closest('.modal-backdrop'); if (m) m.hidden = true; return; }
    if (e.target.classList && e.target.classList.contains('modal-backdrop') && e.target.id !== 'confirmModal') e.target.hidden = true;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') document.querySelectorAll('.modal-backdrop:not([hidden])').forEach((m) => { m.hidden = true; });
  });

  /* ---- confirmation dialog (returns a Promise<boolean>) ---- */
  SG.confirm = function ({ title, message, confirmLabel }) {
    return new Promise((resolve) => {
      const modal = document.getElementById('confirmModal');
      document.getElementById('confirmTitle').textContent = title || 'Are you sure?';
      document.getElementById('confirmText').textContent = message || '';
      const yes = document.getElementById('confirmYes'), no = document.getElementById('confirmNo');
      yes.textContent = confirmLabel || 'Delete';
      const done = (v) => { modal.hidden = true; yes.onclick = no.onclick = null; resolve(v); };
      yes.onclick = () => done(true); no.onclick = () => done(false);
      modal.hidden = false; no.focus();
    });
  };

  /* ---- forms ---- */
  SG.clearErrors = (form) => {
    form.querySelectorAll('.field.invalid').forEach((f) => f.classList.remove('invalid'));
    form.querySelectorAll('.field-error').forEach((e) => e.remove());
  };
  SG.setErrors = (form, errors) => {
    SG.clearErrors(form);
    let first = null;
    Object.entries(errors || {}).forEach(([name, msg]) => {
      const input = form.querySelector(`[name="${name}"]`);
      if (!input) return;
      const field = input.closest('.field');
      if (!field) return;
      field.classList.add('invalid');
      const div = document.createElement('div'); div.className = 'field-error'; div.textContent = msg;
      field.appendChild(div);
      first = first || input;
    });
    if (first) first.focus();
  };
  SG.formData = (form) => {
    const out = {};
    new FormData(form).forEach((v, k) => { out[k] = typeof v === 'string' ? v.trim() : v; });
    return out;
  };
  SG.fillForm = (form, data) => {
    form.querySelectorAll('[name]').forEach((el) => { el.value = data && data[el.name] != null ? data[el.name] : ''; });
  };
  /* Native validation first; returns true when the form may be submitted. */
  SG.checkForm = (form) => { SG.clearErrors(form); if (form.checkValidity()) return true; form.reportValidity(); return false; };

  /* ---- pager ---- */
  SG.renderPager = function (el, data, onPage) {
    const { page, pages, total, per_page } = data;
    const from = total ? (page - 1) * per_page + 1 : 0, to = Math.min(total, page * per_page);
    let btns = `<button ${page <= 1 ? 'disabled' : ''} data-p="${page - 1}" aria-label="Previous page">&lsaquo;</button>`;
    const nums = new Set([1, pages, page - 1, page, page + 1]);
    let last = 0;
    [...nums].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b).forEach((n) => {
      if (n - last > 1) btns += '<button disabled>…</button>';
      btns += `<button class="${n === page ? 'current' : ''}" data-p="${n}">${n}</button>`; last = n;
    });
    btns += `<button ${page >= pages ? 'disabled' : ''} data-p="${page + 1}" aria-label="Next page">&rsaquo;</button>`;
    el.innerHTML = `<span>${total ? `Showing ${from}–${to} of ${SG.num(total)}` : 'No records'}</span>` + (pages > 1 ? `<div class="pager-btns">${btns}</div>` : '');
    el.querySelectorAll('button[data-p]').forEach((b) => b.addEventListener('click', () => onPage(Number(b.dataset.p))));
  };

  /* ---- shared table states ---- */
  SG.loadingRow = (tbody, cols) => { tbody.innerHTML = `<tr><td colspan="${cols}" class="loading">Loading…</td></tr>`; };
  SG.emptyRow = (tbody, cols, msg) => { tbody.innerHTML = `<tr><td colspan="${cols}" class="empty-cell">${SG.esc(msg)}</td></tr>`; };
  SG.errorRow = (tbody, cols, msg) => { tbody.innerHTML = `<tr><td colspan="${cols}" class="empty-cell" style="color:var(--red)">${SG.esc(msg)}</td></tr>`; };
  SG.summaryStrip = (el, items) => { el.innerHTML = items.map((i) => `<div class="summary-item"><span>${SG.esc(i.label)}</span><strong>${SG.esc(i.value)}</strong></div>`).join(''); };

  /* ---- chrome: mobile sidebar, global search, notifications ---- */
  const sidebar = document.getElementById('sidebar'), backdrop = document.getElementById('sidebarBackdrop');
  const toggleNav = (open) => { sidebar.classList.toggle('open', open); backdrop.classList.toggle('open', open); };
  document.getElementById('menuBtn').addEventListener('click', () => toggleNav(!sidebar.classList.contains('open')));
  backdrop.addEventListener('click', () => toggleNav(false));

  const search = document.getElementById('globalSearch'), results = document.getElementById('searchResults');
  const runSearch = SG.debounce(async () => {
    const q = search.value.trim();
    if (q.length < 2) { results.hidden = true; return; }
    try {
      const d = await SG.api('GET', '/api/search?q=' + encodeURIComponent(q));
      let html = '';
      if (d.customers.length) html += '<h4>Customers</h4>' + d.customers.map((c) => `<a href="/customers?q=${encodeURIComponent(c.full_name)}">${SG.esc(c.full_name)}<small>${SG.esc(c.email || '')}</small></a>`).join('');
      if (d.policies.length) html += '<h4>Policies</h4>' + d.policies.map((p) => `<a href="/policies?q=${p.policy_id}">Policy #${p.policy_id} — ${SG.esc(p.customer_name)}<small>${SG.esc(p.policy_type)}</small></a>`).join('');
      results.innerHTML = html || '<div class="empty">No matching customers or policies.</div>';
      results.hidden = false;
    } catch (e) { results.hidden = true; }
  }, 250);
  search.addEventListener('input', runSearch);

  const bell = document.getElementById('bellBtn'), panel = document.getElementById('notifPanel'), count = document.getElementById('bellCount');
  async function loadNotifications() {
    try {
      const n = await SG.api('GET', '/api/notifications');
      count.hidden = n.count === 0; count.textContent = n.count > 9 ? '9+' : n.count;
      let html = '';
      if (n.pending_total) html += `<h4>Pending claims (${n.pending_total})</h4>` + n.pending_claims.map((c) => `<a href="/claims?status=Pending">Claim #${c.claim_id} — ${SG.esc(c.customer_name)}<small>${SG.money(c.claim_amount)} awaiting review</small></a>`).join('');
      if (n.expiring_total) html += `<h4>Policies expiring within 30 days (${n.expiring_total})</h4>` + n.expiring.map((p) => `<a href="/policies?q=${p.policy_id}">Policy #${p.policy_id} — ${SG.esc(p.customer_name)}<small>Ends ${SG.date(p.end_date)}</small></a>`).join('');
      panel.innerHTML = html || '<div class="empty">You are all caught up.</div>';
    } catch (e) { panel.innerHTML = '<div class="empty">Notifications are unavailable.</div>'; }
  }
  bell.addEventListener('click', (e) => { e.stopPropagation(); panel.hidden = !panel.hidden; });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.bell-wrap')) panel.hidden = true;
    if (!e.target.closest('.search-box')) results.hidden = true;
  });
  loadNotifications();
  SG.refreshNotifications = loadNotifications;
})();
