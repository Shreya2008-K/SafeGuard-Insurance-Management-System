/* Reports page: GET /api/reports/<name> with filters; client-side sortable tables; print layout via CSS. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const FILTERS = {
    customers: ['q', 'date_from', 'date_to'], agents: ['q'], 'customer-history': ['customer_id'],
    policies: ['q', 'policy_type', 'status', 'date_from', 'date_to'], 'policies-active': ['q', 'policy_type', 'date_from', 'date_to'],
    'policies-expired': ['q', 'policy_type', 'date_from', 'date_to'], 'by-type': ['min_count'],
    'claims-pending': ['policy_id', 'date_from', 'date_to'], 'claims-approved': ['policy_id', 'date_from', 'date_to'],
    'claims-rejected': ['policy_id', 'date_from', 'date_to'], totals: [],
  };
  const INPUT = { q: 'fq', customer_id: 'fcustomer', policy_type: 'ftype', status: 'fstatus', policy_id: 'fpolicy', min_count: 'fmin', date_from: 'ffrom', date_to: 'fto' };
  let current = null, sortKey = null, sortDir = 1;

  function syncFilters() {
    const used = FILTERS[$('reportSelect').value];
    document.querySelectorAll('[data-filter]').forEach((el) => { el.hidden = !used.includes(el.dataset.filter); });
    const isClaim = $('reportSelect').value.startsWith('claims'), isCust = $('reportSelect').value === 'customers';
    $('flabelFrom').textContent = isClaim ? 'Claim date from' : isCust ? 'Joined from' : 'Start date from';
    $('fq').placeholder = $('reportSelect').value.startsWith('policies') ? 'Policy ID or customer' : 'Name or email';
  }

  function cell(col, v) {
    if (v == null || v === '') return '—';
    if (col.type === 'money') return SG.money(v);
    if (col.type === 'date') return SG.date(v);
    if (col.type === 'badge') return SG.badge(v);
    if (col.type === 'number') return SG.num(v);
    return SG.esc(v);
  }
  function renderTable() {
    const { columns, rows } = current;
    let data = rows.slice();
    if (sortKey) {
      const col = columns.find((c) => c.key === sortKey), numeric = ['money', 'number'].includes(col.type);
      data.sort((a, b) => { const x = a[sortKey], y = b[sortKey]; if (x == null) return 1; if (y == null) return -1; return (numeric ? x - y : String(x).localeCompare(String(y))) * sortDir; });
    }
    $('reportTable').innerHTML = `<thead><tr>${columns.map((c) => `<th data-key="${c.key}" class="${['money', 'number'].includes(c.type) ? 'num' : ''} ${sortKey === c.key ? (sortDir > 0 ? 'asc' : 'desc') : ''}">${SG.esc(c.label)}</th>`).join('')}</tr></thead>
      <tbody>${data.length ? data.map((r) => `<tr>${columns.map((c) => `<td class="${['money', 'number'].includes(c.type) ? 'num' : ''}">${cell(c, r[c.key])}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${columns.length}" class="empty-cell">No records match these filters.</td></tr>`}</tbody>`;
  }

  async function run() {
    const name = $('reportSelect').value, qs = new URLSearchParams();
    FILTERS[name].forEach((k) => { const v = $(INPUT[k]).value.trim(); if (v) qs.set(k, v); });
    if (name === 'customer-history' && !qs.get('customer_id')) { SG.toast('Select a customer first.', 'error'); return; }
    $('runBtn').disabled = true; $('reportTable').innerHTML = '<tbody><tr><td class="loading">Loading…</td></tr></tbody>';
    try {
      current = await SG.api('GET', `/api/reports/${name}?${qs}`);
      sortKey = null;
      $('reportTitle').textContent = current.title;
      $('reportMeta').textContent = `${current.rows.length} row${current.rows.length === 1 ? '' : 's'} · generated ${new Date().toLocaleString('en-GB')}`;
      SG.summaryStrip($('reportSummary'), current.summary.map((s) => ({ label: s.label, value: s.type === 'money' ? SG.money(s.value) : SG.num(s.value) })));
      renderTable();
    } catch (e) { $('reportTable').innerHTML = `<tbody><tr><td class="empty-cell" style="color:var(--red)">${SG.esc(e.message)}</td></tr></tbody>`; }
    finally { $('runBtn').disabled = false; }
  }

  $('reportTable').addEventListener('click', (e) => {
    const th = e.target.closest('th[data-key]');
    if (!th || !current) return;
    sortDir = sortKey === th.dataset.key ? -sortDir : 1; sortKey = th.dataset.key; renderTable();
  });
  $('reportSelect').addEventListener('change', () => { syncFilters(); run(); });
  $('runBtn').addEventListener('click', run);
  document.querySelectorAll('.filters-grid input').forEach((i) => i.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); }));
  $('printBtn').addEventListener('click', () => window.print());

  SG.api('GET', '/api/lookups').then((l) => {
    $('fcustomer').innerHTML = '<option value="">Select customer…</option>' + l.customers.map((c) => `<option value="${c.customer_id}">${SG.esc(c.full_name)} (#${c.customer_id})</option>`).join('');
  }).catch(() => {});
  syncFilters(); run();
})();
