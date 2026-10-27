(function () {
  function getPreferredTheme() {
    let stored = null;
    try { stored = localStorage.getItem('theme'); } catch (e) {}
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

  // Follow the device setting live unless the visitor picked a theme themselves.
  try {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      let stored = null;
      try { stored = localStorage.getItem('theme'); } catch (e) {}
      if (!stored && darkModeEnabled) applyTheme(getPreferredTheme());
    });
  } catch (e) {}

  let darkModeEnabled = true; // optimistic default until settings load
  let domReady = false;

  const SUN = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  const MOON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/></svg>';

  function toggleIcon(theme) {
    return theme === 'dark' ? SUN : MOON;
  }

  function maybeRenderToggle() {
    if (!domReady || !darkModeEnabled) return;
    if (document.querySelector('.theme-toggle')) return;

    const btn = document.createElement('button');
    btn.className = 'theme-toggle';
    btn.type = 'button';
    btn.setAttribute('aria-label', document.documentElement.getAttribute('data-theme') === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    btn.title = 'Light / dark mode';
    btn.innerHTML = toggleIcon(document.documentElement.getAttribute('data-theme'));

    btn.addEventListener('click', () => {
      const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      try { localStorage.setItem('theme', next); } catch (e) {}
      btn.innerHTML = toggleIcon(next);
      btn.setAttribute('aria-label', next === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
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
