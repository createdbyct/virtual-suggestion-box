// Sign-in pages: if there's already a valid session, go straight to the
// dashboard instead of showing a login form. The page content stays hidden
// for the brief check (and for at most 1.5s if the server is slow).
(function () {
  var root = document.documentElement;
  root.classList.add('auth-pending');
  var settled = false;
  function show() { if (settled) return; settled = true; root.classList.remove('auth-pending'); }
  setTimeout(show, 1500);
  fetch('/api/auth/me', { credentials: 'same-origin' })
    .then(function (r) { if (r.ok) { window.location.replace('/dashboard'); } else { show(); } })
    .catch(show);
})();
