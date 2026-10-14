/* Create Account page: show/hide password, client-side checks, disabled state while submitting.
   The server repeats every check and only ever creates STAFF accounts from this page. */
(function () {
  'use strict';
  const form = document.getElementById('registerForm'), err = document.getElementById('clientError');
  document.querySelectorAll('[data-toggle]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const input = document.getElementById(btn.dataset.toggle), show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      btn.style.color = show ? 'var(--teal)' : '';
    });
  });
  form.addEventListener('submit', (e) => {
    const u = form.username.value.trim(), p = form.password.value, c = form.confirm_password.value;
    let msg = '';
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{2,29}$/.test(u)) msg = 'Username must be 3-30 characters: letters, numbers, dot, underscore or hyphen.';
    else if (p.length < 8 || !/[A-Za-z]/.test(p) || !/\d/.test(p)) msg = 'Password needs at least 8 characters with a letter and a number.';
    else if (p !== c) msg = 'Passwords do not match.';
    if (msg) { e.preventDefault(); err.textContent = msg; err.hidden = false; return; }
    const btn = document.getElementById('registerBtn');
    btn.disabled = true; btn.textContent = 'Creating account…';
  });
})();
