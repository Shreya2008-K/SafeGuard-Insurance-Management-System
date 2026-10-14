/* Settings page (admin only): database counts and environment. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  SG.api('GET', '/api/settings').then((s) => {
    const labels = { APP_USER: 'Users', CUSTOMER: 'Customers', INSURANCE_AGENT: 'Agents', INSURANCE_POLICY: 'Policies', CLAIM: 'Claims' };
    $('dbInfo').innerHTML = `<dt>File</dt><dd>${SG.esc(s.database)}</dd>` + Object.entries(s.counts).map(([k, v]) => `<dt>${labels[k]}</dt><dd>${SG.num(v)}</dd>`).join('');
    $('sysInfo').innerHTML = `<dt>Environment</dt><dd>${SG.esc(s.environment)}</dd>`;
  }).catch((e) => SG.toast(e.message, 'error'));
})();
