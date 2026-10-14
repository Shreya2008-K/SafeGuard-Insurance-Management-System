/* SafeGuard IMS - login page behaviour (UI only; authentication happens on the server). */
(function () {
  'use strict';
  const form = document.getElementById('loginForm'), pw = document.getElementById('password');
  const toggle = document.getElementById('togglePw'), err = document.getElementById('clientError');
  toggle.addEventListener('click', () => {
    const show = pw.type === 'password';
    pw.type = show ? 'text' : 'password';
    toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    toggle.style.color = show ? 'var(--teal)' : '';
  });
  form.addEventListener('submit', (e) => {
    const u = document.getElementById('username').value.trim();
    if (!u || !pw.value) {
      e.preventDefault();
      err.textContent = 'Enter your username and password.'; err.hidden = false;
      (u ? pw : document.getElementById('username')).focus();
      return;
    }
    document.getElementById('signInBtn').disabled = true;
    document.getElementById('signInBtn').textContent = 'Signing in…';
  });
})();
