(function () {
  function getPreferredTheme() {
    const stored = localStorage.getItem('theme');
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
  }

  // Applied immediately (script runs in <head>) so there's no flash of the
  // wrong theme before the page paints. If dark mode turns out to be
  // disabled site-wide, this gets corrected once /api/settings resolves.
  applyTheme(getPreferredTheme());

  let darkModeEnabled = true; // optimistic default until settings load
  let domReady = false;

  function maybeRenderToggle() {
    if (!domReady || !darkModeEnabled) return;
    if (document.querySelector('.theme-toggle')) return;

    const btn = document.createElement('button');
    btn.className = 'theme-toggle';
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Toggle dark mode');
    btn.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? '☀️' : '🌙';

    btn.addEventListener('click', () => {
      const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      localStorage.setItem('theme', next);
      btn.textContent = next === 'dark' ? '☀️' : '🌙';
    });

    document.body.appendChild(btn);
  }

  // Shared footer on every page — base tagline is always there; the
  // superadmin-configured credit line (if any) gets appended once
  // /api/settings resolves.
  function renderFooter() {
    if (document.querySelector('.site-footer')) return;

    const footer = document.createElement('footer');
    footer.className = 'site-footer';
    footer.id = 'site-footer';
    footer.textContent = 'Virtual Suggestion Box — self-hosted suggestion & nomination forms.';
    document.body.appendChild(footer);
  }

  function appendFooterCredit(settings) {
    if (!settings.footer_text) return;
    const footer = document.getElementById('site-footer');
    if (!footer) return;

    const credit = document.createElement('div');
    credit.style.marginTop = '6px';
    if (settings.footer_link_url) {
      const a = document.createElement('a');
      a.href = settings.footer_link_url;
      a.textContent = settings.footer_text;
      a.style.color = 'inherit';
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      credit.appendChild(a);
    } else {
      credit.textContent = settings.footer_text;
    }
    footer.appendChild(credit);
  }

  window.addEventListener('DOMContentLoaded', () => {
    domReady = true;
    renderFooter();
    maybeRenderToggle();
  });

  fetch('/api/settings')
    .then(res => res.ok ? res.json() : {})
    .catch(() => ({}))
    .then(settings => {
      darkModeEnabled = settings.dark_mode_enabled !== false;
      if (!darkModeEnabled) {
        // Site-wide override — force light regardless of stored preference,
        // and remove the toggle if it already rendered before this resolved.
        applyTheme('light');
        const existing = document.querySelector('.theme-toggle');
        if (existing) existing.remove();
      } else {
        maybeRenderToggle();
      }
      if (domReady) appendFooterCredit(settings);
      else window.addEventListener('DOMContentLoaded', () => appendFooterCredit(settings));
    });
})();
