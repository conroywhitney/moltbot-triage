/* app.js — Shared utilities for Moltbot Triage dashboard */

function dataPath(filename) {
  const path = window.location.pathname;
  const base = /\/(prs|issues)\//.test(path) ? '..' : '.';
  return base + '/data/' + filename;
}

async function fetchData(filename) {
  const resp = await fetch(dataPath(filename));
  if (!resp.ok) throw new Error(`Failed to load ${filename}: ${resp.status}`);
  return resp.json();
}

function timeAgo(iso) {
  if (!iso) return '?';
  const now = Date.now();
  const then = new Date(iso).getTime();
  const diff = Math.floor((now - then) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  const days = Math.floor(diff / 86400);
  if (days === 1) return '1 day ago';
  return days + ' days ago';
}

function daysAgo(iso) {
  if (!iso) return 9999;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

function escapeHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderBadge(type, value) {
  if (type === 'size') {
    const cls = 'badge badge-' + (value || 'unknown');
    return `<span class="${cls}">${escapeHtml(value)}</span>`;
  }
  if (type === 'ci') {
    const cls = 'ci-dot ci-' + (value || 'unknown');
    return `<span class="${cls}" title="${escapeHtml(value)}"></span>`;
  }
  if (type === 'label') {
    return `<span class="badge badge-label">${escapeHtml(value)}</span>`;
  }
  return `<span class="badge">${escapeHtml(value)}</span>`;
}

function renderStatCard(label, value, subtext) {
  let html = `<div class="stat-card">
    <div class="stat-value">${escapeHtml(String(value))}</div>
    <div class="stat-label">${escapeHtml(label)}</div>`;
  if (subtext) html += `<div class="stat-subtext">${escapeHtml(subtext)}</div>`;
  html += '</div>';
  return html;
}

function renderBarChart(containerId, data, options = {}) {
  const container = document.getElementById(containerId);
  if (!container || !data || data.length === 0) {
    if (container) container.innerHTML = '<div class="empty-state">No data</div>';
    return;
  }
  const maxVal = Math.max(...data.map(d => d.value));
  const color = options.color || '#818cf8';

  let html = '<div class="bar-chart">';
  for (const item of data) {
    const pct = maxVal > 0 ? (100 * item.value / maxVal) : 0;
    const barColor = item.color || color;
    html += `<div class="bar-row">
      <span class="bar-label" title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${barColor}"></div></div>
      <span class="bar-value">${item.value}</span>
    </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

function renderTable(containerId, data, columns, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!data || data.length === 0) {
    container.innerHTML = options.emptyMessage
      ? `<div class="empty-state">${escapeHtml(options.emptyMessage)}</div>`
      : '<div class="empty-state">No data found.</div>';
    return;
  }

  const tableId = options.tableId || (containerId + '-table');
  let html = `<div class="table-wrapper"><table id="${tableId}"><thead><tr>`;

  columns.forEach((col, i) => {
    const sortType = col.sortType || 'str';
    html += `<th onclick="sortTableByCol('${tableId}',${i},'${sortType}')">${escapeHtml(col.header)} <span class="sort-arrow"></span></th>`;
  });

  html += '</tr></thead><tbody>';

  for (const row of data) {
    const attrs = (options.rowAttrs ? options.rowAttrs(row) : '') || '';
    html += `<tr ${attrs}>`;
    for (const col of columns) {
      const val = col.render ? col.render(row) : escapeHtml(String(row[col.key] || ''));
      const cls = col.className ? ` class="${col.className}"` : '';
      const sortVal = col.sortValue ? ` data-sort="${col.sortValue(row)}"` : '';
      html += `<td${cls}${sortVal}>${val}</td>`;
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Table sorting (global) ──────────────────────────────────
const _sortState = {};
function sortTableByCol(tableId, col, type) {
  const table = document.getElementById(tableId);
  if (!table) return;
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);

  const key = tableId + '-' + col;
  const asc = _sortState[key] === 'asc' ? false : true;
  _sortState[key] = asc ? 'asc' : 'desc';

  rows.sort((a, b) => {
    let va = a.cells[col].getAttribute('data-sort') || a.cells[col].textContent.trim();
    let vb = b.cells[col].getAttribute('data-sort') || b.cells[col].textContent.trim();
    if (type === 'num') {
      va = parseFloat(va.replace(/[^0-9.\-]/g, '')) || 0;
      vb = parseFloat(vb.replace(/[^0-9.\-]/g, '')) || 0;
      return asc ? va - vb : vb - va;
    }
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  });

  rows.forEach(r => tbody.appendChild(r));

  // Update arrows
  table.querySelectorAll('.sort-arrow').forEach(s => s.textContent = '');
  const arrow = table.rows[0].cells[col].querySelector('.sort-arrow');
  if (arrow) arrow.textContent = asc ? '▲' : '▼';
}

function setupSearch(inputId, tableId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.addEventListener('input', () => {
    const q = input.value.toLowerCase();
    const rows = document.querySelectorAll(`#${tableId} tbody tr`);
    rows.forEach(row => {
      const text = row.textContent.toLowerCase();
      row.classList.toggle('hidden', q && !text.includes(q));
    });
  });
}

function setupFilters(buttonClass, tableId, attrName) {
  document.querySelectorAll('.' + buttonClass).forEach(btn => {
    btn.addEventListener('click', () => {
      const group = btn.getAttribute('data-group');
      // Deactivate siblings
      document.querySelectorAll(`.${buttonClass}[data-group="${group}"]`).forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyAllFilters(tableId);
    });
  });
}

function applyAllFilters(tableId) {
  const activeFilters = {};
  document.querySelectorAll('.filter-btn.active').forEach(btn => {
    const group = btn.getAttribute('data-group');
    const value = btn.getAttribute('data-value');
    if (group) activeFilters[group] = value;
  });

  const rows = document.querySelectorAll(`#${tableId} tbody tr`);
  rows.forEach(row => {
    let show = true;
    for (const [group, value] of Object.entries(activeFilters)) {
      if (value === 'all') continue;
      if (row.getAttribute('data-' + group) !== value) {
        show = false;
        break;
      }
    }
    // Also respect search
    const searchInput = document.querySelector('.search-box');
    if (show && searchInput && searchInput.value) {
      const q = searchInput.value.toLowerCase();
      if (!row.textContent.toLowerCase().includes(q)) show = false;
    }
    row.classList.toggle('hidden', !show);
  });
}

function prLink(pr) {
  return `<a href="${escapeHtml(pr.url)}" target="_blank">#${pr.number}</a>`;
}

function issueLink(num, url) {
  const href = url || `https://github.com/moltbot/moltbot/issues/${num}`;
  return `<a href="${escapeHtml(href)}" target="_blank">#${num}</a>`;
}
