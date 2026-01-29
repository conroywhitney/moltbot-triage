/* nav.js — Shared navigation component */
(function () {
  const pages = [
    { href: '/index.html', label: 'Overview', match: /\/(index\.html)?$/ },
    { href: '/prs/ready.html', label: 'Ready', match: /\/prs\/ready\.html$/ },
    { href: '/prs/failing.html', label: 'CI Failing', match: /\/prs\/failing\.html$/ },
    { href: '/prs/huge.html', label: 'Huge PRs', match: /\/prs\/huge\.html$/ },
    { href: '/prs/all.html', label: 'All PRs', match: /\/prs\/all\.html$/ },
    { href: '/issues/trending.html', label: 'Trending', match: /\/issues\/trending\.html$/ },
    { href: '/health.html', label: 'Health', match: /\/health\.html$/ },
  ];

  function getBasePath() {
    const path = window.location.pathname;
    // Detect if we're in a subdirectory (prs/ or issues/)
    if (/\/(prs|issues)\//.test(path)) return '..';
    return '.';
  }

  function renderNav() {
    const base = getBasePath();
    const path = window.location.pathname;
    const nav = document.createElement('nav');
    nav.className = 'top-nav';
    nav.setAttribute('role', 'navigation');

    let linksHTML = pages.map(p => {
      const href = base + p.href;
      const active = p.match.test(path) ? ' active' : '';
      return `<a href="${href}" class="nav-link${active}">${p.label}</a>`;
    }).join('');

    nav.innerHTML = `
      <div class="nav-inner">
        <a href="${base}/index.html" class="nav-brand">🔮 Moltbot Triage</a>
        ${linksHTML}
        <span class="nav-meta" id="nav-generated"></span>
      </div>`;

    document.body.prepend(nav);

    // Load generated time
    fetch(base + '/data/meta.json')
      .then(r => r.json())
      .then(meta => {
        const el = document.getElementById('nav-generated');
        if (el && meta.generated) {
          el.textContent = 'Updated ' + timeAgo(meta.generated);
        }
      })
      .catch(() => {});
  }

  function timeAgo(iso) {
    const now = Date.now();
    const then = new Date(iso).getTime();
    const diff = Math.floor((now - then) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderNav);
  } else {
    renderNav();
  }
})();
