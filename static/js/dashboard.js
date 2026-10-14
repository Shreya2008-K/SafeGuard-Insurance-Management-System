/* Dashboard: every figure comes from GET /api/dashboard (aggregate SQL queries). */
(function () {
  'use strict';
  const COLORS = { 'Life Insurance': '#18A999', 'Health Insurance': '#4F83E3', 'Vehicle Insurance': '#F2A93B', 'Property Insurance': '#193653' };
  const $ = (id) => document.getElementById(id);

  function renderCards(c) {
    $('statCustomers').textContent = SG.num(c.total_customers);
    $('statPolicies').textContent = SG.num(c.active_policies);
    $('statClaims').textContent = SG.num(c.pending_claims);
    $('statAgents').textContent = SG.num(c.total_agents);
    const note = $('statCustomersNote');
    note.textContent = `${c.customer_growth >= 0 ? '+' : ''}${c.customer_growth.toFixed(1)}% from last month`;
    note.classList.toggle('up', c.customer_growth > 0);
  }

  function renderDonut(dist, total) {
    $('donutLegend').innerHTML = dist.map((d) => `<li><span class="dot" style="background:${COLORS[d.type]}"></span><span>${SG.esc(d.type)}</span><span class="count">${SG.num(d.count)}</span><span class="pct">${d.percent.toFixed(1)}%</span></li>`).join('');
    const centre = {
      id: 'centre',
      afterDraw(chart) {
        const { ctx, chartArea: a } = chart, x = (a.left + a.right) / 2, y = (a.top + a.bottom) / 2;
        ctx.save(); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillStyle = '#17324D'; ctx.font = '700 30px Inter, sans-serif'; ctx.fillText(SG.num(total), x, y - 8);
        ctx.fillStyle = '#78879A'; ctx.font = '400 12px Inter, sans-serif'; ctx.fillText('Active policies', x, y + 16);
        ctx.restore();
      },
    };
    const hasData = total > 0;
    new Chart($('donutChart'), {
      type: 'doughnut',
      data: { labels: dist.map((d) => d.type), datasets: [{ data: hasData ? dist.map((d) => d.count) : [1], backgroundColor: hasData ? dist.map((d) => COLORS[d.type]) : ['#E5EAF0'], borderWidth: 3, borderColor: '#fff', hoverOffset: 4 }] },
      options: { cutout: '70%', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: hasData } } },
      plugins: [centre],
    });
  }

  function renderBars(c) {
    new Chart($('barChart'), {
      type: 'bar',
      data: { labels: c.labels, datasets: [
        { label: 'Approved', data: c.approved, backgroundColor: '#18A999', borderRadius: 4, barPercentage: .8 },
        { label: 'Pending', data: c.pending, backgroundColor: '#F2A93B', borderRadius: 4, barPercentage: .8 },
        { label: 'Rejected', data: c.rejected, backgroundColor: '#D95D6A', borderRadius: 4, barPercentage: .8 }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8, color: '#78879A' } } },
        scales: { x: { grid: { display: false }, ticks: { color: '#78879A' } }, y: { beginAtZero: true, ticks: { precision: 0, color: '#78879A' }, grid: { color: '#E5EAF0' } } } },
    });
  }

  function renderTables(d) {
    $('recentPolicies').innerHTML = d.recent_policies.length ? d.recent_policies.map((p) => `<tr><td><a class="id-link" href="/policies?q=${p.policy_id}">${SG.pid(p.policy_id)}</a></td><td>${SG.esc(p.customer_name)}</td><td>${SG.esc(p.policy_type)}</td><td class="num">${SG.money(p.premium_amount)}</td><td>${SG.date(p.start_date)}</td><td>${SG.badge(p.status)}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">No policies yet. Create your first policy to see it here.</td></tr>';
    $('recentClaims').innerHTML = d.recent_claims.length ? d.recent_claims.map((c) => `<li><span class="claim-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 4.5 6v5.5c0 4.4 3 8.3 7.5 9.5 4.5-1.2 7.5-5.1 7.5-9.5V6z"/></svg></span><div class="claim-main"><strong>${SG.cid(c.claim_id)}</strong><small>${SG.date(c.claim_date)}</small></div><div class="claim-side"><strong>${SG.money(c.claim_amount)}</strong>${SG.badge(c.claim_status)}</div></li>`).join('') : '<li class="empty-cell">No claims submitted yet.</li>';
  }

  SG.api('GET', '/api/dashboard').then((d) => {
    renderCards(d.cards); renderDonut(d.distribution, d.total_active); renderBars(d.claims_chart); renderTables(d);
  }).catch((e) => SG.toast(e.message, 'error'));
})();
