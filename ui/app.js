const $ = id => document.getElementById(id);
const HISTORY_LEN = 40;
const series = { cpu: [], ram: [], rtt: [], bat: [], up: [], down: [] };
const trend = { pred: [], act: [] };
let lastSys = { cpu: 35, ram: 58, rtt: 60, bat: 82 };
let lastCores = [], lastSysFull = {};
const push = (arr, v) => { arr.push(v); if (arr.length > HISTORY_LEN) arr.shift(); };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const MINUS = '−';

/* ---------------- persistent state (browser only; every access guarded) ---------------- */
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
const DEFAULT_SETTINGS = { mode: 'top3', minMs: 50, disabled: [] };
let settings = { ...DEFAULT_SETTINGS, ...store.get('alo_settings', {}) };
let runs = store.get('alo_history', []);   // newest first, max 100
let runSeq = Date.now();

const predictOpts = (profile) => ({
  ...(profile ? { profile } : {}),
  disabled: settings.disabled,
  ...(settings.mode === 'threshold' ? { min_saving_ms: Number(settings.minMs) || 0 } : {}),
});

/* ---------------- routing ---------------- */
const PAGES = ['overview', 'live', 'agent', 'predictions', 'optimizations', 'history', 'model'];
const loaded = {};
function route() {
  const page = PAGES.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
  document.querySelectorAll('.page').forEach(s => { s.hidden = s.dataset.page !== page; });
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
  if (page === 'live') renderLive();
  if (page === 'agent') renderAgent();
  if (page === 'history') renderHistoryPage();
  if (page === 'optimizations') loadOptimizations();
  if (page === 'model') loadModel();
}
window.addEventListener('hashchange', route);

/* ---------------- charts ---------------- */
function linePath(data, W, H, max, n = HISTORY_LEN) {
  const d = data.filter(v => v != null);
  if (d.length < 2) return '';
  return d.map((v, i) => `${i ? 'L' : 'M'}${(i * W / (n - 1) + (W - (d.length - 1) * W / (n - 1))).toFixed(1)} ${(H - (Math.min(v, max) / max) * (H - 6) - 3).toFixed(1)}`).join(' ');
}
function chartSvg(el, lines, W, H, max, area = true) {
  const grid = [0.25, 0.5, 0.75].map(f => `<line x1="0" x2="${W}" y1="${H * f}" y2="${H * f}" stroke="#dbe7f1" stroke-dasharray="4 5"/>`).join('');
  el.innerHTML = grid + lines.map(l => {
    const p = linePath(l.data, W, H, max);
    if (!p) return '';
    const x0 = (W - (l.data.filter(v => v != null).length - 1) * W / (HISTORY_LEN - 1)).toFixed(1);
    return (area ? `<path d="${p} L${W} ${H} L${x0} ${H} Z" fill="${l.color}" opacity=".10"/>` : '') +
      `<path d="${p}" fill="none" stroke="${l.color}" stroke-width="2.2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
  }).join('');
}

/* ---------------- overview ---------------- */
const STATS = [
  { key: 'cpu', label: 'CPU', unit: '%', fmt: v => v.toFixed(0), color: '#0284c7', max: 100 },
  { key: 'ram', label: 'Memory', unit: '%', fmt: v => v.toFixed(0), color: '#0ea5e9', max: 100 },
  { key: 'rtt', label: 'Network RTT', unit: 'ms', fmt: v => v.toFixed(0), color: '#38bdf8', max: 400 },
  { key: 'bat', label: 'Battery', unit: '%', fmt: v => v.toFixed(0), color: '#16a34a', max: 100 },
];

function renderStats() {
  $('stats').innerHTML = STATS.map(s => {
    const d = series[s.key], v = d[d.length - 1];
    const line = linePath(d, 200, 38, s.max);
    return `<div class="stat"><div class="stat-top"><span>${s.label}</span></div>
      <div class="stat-val">${v == null ? '—' : s.fmt(v)}<small>${v == null ? '' : s.unit}</small></div>
      <svg class="spark" viewBox="0 0 200 38" preserveAspectRatio="none">
        ${line ? `<path d="${line} L200 38 L0 38 Z" fill="${s.color}" opacity=".12"/>` : ''}
        <path d="${line}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/></svg></div>`;
  }).join('');
}

function renderTrend() {
  const act = trend.act.filter(v => v != null);
  const W = 600, H = 220, max = Math.max(4, ...trend.pred, ...act) * 1.15;
  const path = d => d.map((v, i) => {
    const x = d.length < 2 ? 0 : i * W / (d.length - 1);
    return `${i ? 'L' : 'M'}${x.toFixed(1)} ${(H - (v / max) * (H - 16) - 8).toFixed(1)}`;
  }).join(' ');
  const grid = [0.25, 0.5, 0.75].map(f => `<line x1="0" x2="${W}" y1="${H * f}" y2="${H * f}" stroke="#dbe7f1" stroke-dasharray="4 5"/>`).join('');
  $('trend').innerHTML = grid +
    `<path d="${path(trend.pred)}" fill="none" stroke="#0284c7" stroke-width="2.5" vector-effect="non-scaling-stroke"/>` +
    `<path d="${path(act)}" fill="none" stroke="#64748b" stroke-width="2.5" vector-effect="non-scaling-stroke"/>`;
}

const actionState = a => a.disabled ? ['Turned off', 'off'] : a.applied ? ['Applied', 'on'] : ['Not applied', 'off'];

function renderPrediction(p) {
  const maxSec = 12, frac = Math.min(1, p.optimized / maxSec), len = 251.3;
  const fill = $('g-fill');
  fill.style.strokeDasharray = len;
  fill.style.strokeDashoffset = len * (1 - frac);
  fill.style.stroke = p.optimized < 3 ? '#16a34a' : p.optimized < 6 ? '#d97706' : '#dc2626';
  $('pred-val').textContent = p.optimized.toFixed(1);
  const rg = p.range && p.range.optimized;   // 80% range from held-out errors (absent in mock mode / older API)
  $('range-note').hidden = !rg;
  if (rg) {
    const fmt = v => v < 10 ? v.toFixed(1) : v.toFixed(0);
    $('range-note').textContent = `Likely between ${fmt(rg[0])} and ${fmt(rg[1])} s ` +
      `(about ${Math.round(p.range.level * 100)}% of unseen pages fell inside a range like this).`;
  }
  $('base-val').textContent = p.baseline.toFixed(1) + ' s';
  $('opt-val').textContent = p.optimized.toFixed(1) + ' s';
  $('save-val').textContent = `${((p.saved / p.baseline) * 100).toFixed(1)}%`;
  $('profile-chip').textContent = p.network;
  $('note').hidden = !p.warning;
  $('note').textContent = p.warning || '';
  $('actions').innerHTML = p.actions.map(a => {
    const ms = a.savedMs ?? a.savedSec * 1000, [label, cls] = actionState(a);
    return `<li class="${a.applied ? '' : 'skipped'}"><div><b>${a.name}</b><span>${a.detail}</span>
      <div class="tags"><em class="tag method">${a.method || 'Optimization'}</em><em class="tag ${cls}">${label}</em></div></div>
      <span class="gain">${MINUS}${ms.toFixed(0)} ms</span></li>`;
  }).join('');
  $('save-ms').textContent = `${MINUS}${(p.saved * 1000).toFixed(0)} ms`;
  const top = Math.max(1, ...p.bytes.map(b => b.bytes));
  $('bytes').innerHTML = [...p.bytes].sort((a, b) => b.bytes - a.bytes).map(b =>
    `<div class="bar-row"><span>${b.type}</span><div class="bar"><i style="width:${(b.bytes / top * 100).toFixed(0)}%"></i></div>
     <em>${(b.bytes / 1024).toFixed(0)} KB</em></div>`).join('');
}

const methodTags = p => p.actions.filter(a => a.applied).map(a => `<em class="tag method">${a.method || 'Optimization'}</em>`).join('') || '<span class="fine">none</span>';

function runRow(r, withActual) {
  const hasAct = r.actual != null;
  const err = hasAct ? Math.abs(r.optimized - r.actual) / r.actual * 100 : null;
  const cls = err == null ? '' : err < 10 ? 'ok' : err < 20 ? 'mid' : 'bad';
  const extra = withActual
    ? `<td>${r.optimized.toFixed(2)} s</td><td>${hasAct ? r.actual.toFixed(2) + ' s' : '—'}</td><td>${hasAct ? `<span class="pill ${cls}">${err.toFixed(0)}%</span>` : '—'}</td>`
    : `<td>${r.baseline.toFixed(2)} s</td><td>${r.optimized.toFixed(2)} s</td>`;
  return `<tr class="clickable" data-id="${r.id}" tabindex="0" title="Click for details"><td>${esc(r.time)}</td><td class="url">${esc(r.url)}</td>
    <td>${esc(r.network)}</td><td><div class="tags">${methodTags(r)}</div></td>
    <td class="gain">${MINUS}${(r.saved * 1000).toFixed(0)} ms</td>${extra}</tr>`;
}

function renderRecent() { $('history').innerHTML = runs.slice(0, 8).map(r => runRow(r, true)).join(''); }

function recordRun(p) {
  runs.unshift({ ...p, id: 'r' + (++runSeq), time: new Date().toLocaleString() });
  runs = runs.slice(0, 100);
  store.set('alo_history', runs);
}

/* ---------------- details drawer ---------------- */
function openDetails(id) {
  const p = runs.find(r => r.id === id);
  if (!p) return;
  $('d-title').textContent = p.url;
  $('d-sub').textContent = `${p.time} · ${p.network} · baseline ${p.baseline.toFixed(2)} s → ${p.optimized.toFixed(2)} s (${MINUS}${(p.saved * 1000).toFixed(0)} ms)`;
  const acts = p.actions.map(a => {
    const [label, cls] = actionState(a);
    return `<div class="why ${a.applied ? '' : 'skipped'}">
      <div class="why-top"><b>${a.name}</b><span class="gain">${MINUS}${(a.savedMs ?? a.savedSec * 1000).toFixed(0)} ms</span></div>
      <div class="tags"><em class="tag method">${a.method || 'Optimization'}</em><em class="tag ${cls}">${label}</em></div>
      <p>${esc(a.reason || a.detail)}</p></div>`;
  }).join('');
  const path = p.path && p.path.length
    ? `<h3>How the model reached its prediction</h3><ol class="path">${p.path.map(x => `<li>${esc(x)}</li>`).join('')}</ol>${p.path_note ? `<p class="fine">${esc(p.path_note)}</p>` : ''}` : '';
  const src = p.source
    ? `<p class="fine">Page data: ${p.source === 'dataset' ? 'measured runs of this site' : 'median of all sites (this URL was not in the training data)'}.</p>` : '';
  $('d-body').innerHTML = `<h3>Why these methods</h3>${acts}${path}${src}
    <p class="fine">Savings are <b>estimates</b> (removed bytes ÷ network throughput). Measured savings need an A/B run.</p>`;
  $('drawer').hidden = false;
  $('drawer-bg').hidden = false;
}
const closeDetails = () => { $('drawer').hidden = true; $('drawer-bg').hidden = true; };
document.addEventListener('click', e => { const tr = e.target.closest('tr[data-id]'); if (tr) openDetails(tr.dataset.id); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeDetails();
  if (e.key === 'Enter') { const tr = e.target.closest && e.target.closest('tr[data-id]'); if (tr) openDetails(tr.dataset.id); }
});
$('d-close').addEventListener('click', closeDetails);
$('drawer-bg').addEventListener('click', closeDetails);

/* ---------------- live system page ---------------- */
function renderLive() {
  const last = k => series[k][series[k].length - 1];
  const f = (v, u, d = 0) => v == null ? '—' : v.toFixed(d) + u;
  $('l-cpu').textContent = f(last('cpu'), '%');
  $('l-ram').textContent = f(last('ram'), '%');
  $('l-rtt').textContent = f(last('rtt'), ' ms');
  const dn = last('down'), up = last('up');
  $('l-net').textContent = dn == null ? '—' : `${(dn / 1000).toFixed(1)} / ${(up / 1000).toFixed(1)} Mbps`;
  chartSvg($('c-cpu'), [{ data: series.cpu, color: '#0284c7' }], 600, 140, 100);
  chartSvg($('c-ram'), [{ data: series.ram, color: '#0ea5e9' }], 600, 140, 100);
  chartSvg($('c-rtt'), [{ data: series.rtt, color: '#38bdf8' }], 600, 140, Math.max(100, ...series.rtt.filter(v => v != null)) * 1.2);
  const nmax = Math.max(1000, ...series.down.filter(v => v != null), ...series.up.filter(v => v != null)) * 1.2;
  chartSvg($('c-net'), [{ data: series.down, color: '#0284c7' }, { data: series.up, color: '#94a3b8' }], 600, 140, nmax, false);
  $('cores').innerHTML = lastCores.map((c, i) =>
    `<div class="core" title="Core ${i}: ${c.toFixed(0)}%"><i style="height:${Math.max(3, c)}%"></i></div>`).join('');
  const s = lastSysFull;
  $('ram-detail').textContent = s.ram_total_gb ? `${s.ram_used_gb} GB used of ${s.ram_total_gb} GB` : 'Sample data (API offline).';
  $('live-profile').textContent = s.network || 'Mock data';
  $('battery-line').textContent = s.bat == null ? 'No battery detected (desktop). Battery readings are skipped.'
    : `Battery ${s.bat}% · ${s.plugged ? 'plugged in' : 'on battery'}.`;
}


/* ---------------- OS agent (system-wide telemetry) ---------------- */
let agentTimer = null;
async function renderAgent() {
  clearInterval(agentTimer);
  agentTimer = setInterval(() => { if (location.hash === '#agent') renderAgent(); else clearInterval(agentTimer); }, 4000);
  let st, ser;
  try {
    st = await (await fetch('/api/agent/status')).json();
    ser = (await (await fetch('/api/agent/series?n=120')).json()).rows;
  } catch { $('ag-state').textContent = 'API offline'; return; }
  const on = st.enabled, last = st.last;
  $('ag-toggle').checked = on;
  $('ag-state').textContent = on ? 'ALO is ON' : 'ALO is OFF';
  $('ag-profile').textContent = last ? (NETS.find(n => n[0] === last.profile) || [0, last.profile])[1] : '--';
  $('ag-rtt').textContent = last && last.rtt_ms != null ? `latency ${last.rtt_ms.toFixed(0)} ms` : '';
  $('ag-apps').textContent = last ? last.apps_online : '--';
  $('ag-conns').textContent = last ? `${last.connections} open connections` : '';
  $('ag-net').textContent = last ? `${(last.down_kbps / 1000).toFixed(1)} / ${(last.up_kbps / 1000).toFixed(1)} Mbps` : '--';
  $('ag-samples').textContent = st.samples.toLocaleString();
  $('ag-up').textContent = `every ${st.interval_s} s, running ${Math.floor(st.uptime_s / 60)} min` + (st.error ? ` - error: ${st.error}` : '');
  $('ag-rtt-big').textContent = last && last.rtt_ms != null ? last.rtt_ms.toFixed(0) + ' ms' : '--';
  const rtts = ser.map(r => r.rtt_ms);
  chartSvg($('ag-c-rtt'), [{ data: rtts.slice(-HISTORY_LEN), color: '#38bdf8' }], 600, 140, Math.max(100, ...rtts.filter(v => v != null)) * 1.2);
  const r = st.recommendation;
  $('ag-reco').innerHTML = r ? `On a <b>${esc(r.profile)}</b> connection a typical page takes about <b>${r.expected_load_s.toFixed(1)} s</b>;
    with ALO's methods (${r.methods.map(esc).join(', ') || 'none'}) about <b class="good">${r.with_alo_s.toFixed(1)} s</b>.`
    : (on ? 'Waiting for the first sample...' : 'The agent is off, so nothing is being measured.');
  $('ag-table').innerHTML = st.apps.map(a => `<tr><td>${esc(a.app)}</td><td>${a.connections}</td>
    <td>${a.connect_ms == null ? '—' : a.connect_ms.toFixed(0) + ' ms'}</td><td>${a.probes}</td></tr>`).join('')
    || `<tr><td colspan="4" class="fine">${on ? 'No application connections yet.' : 'Agent is off.'}</td></tr>`;
}
$('ag-toggle').addEventListener('change', async e => {
  try { await fetch('/api/agent/toggle', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ enabled: e.target.checked }) }); } catch { /* offline */ }
  renderAgent();
});

/* ---------------- predictions (what-if) ---------------- */
const NETS = [['No_Throttle', 'Wired / Wi-Fi (fast)'], ['Fast_4G', 'Fast 4G-like'], ['Slow_3G', 'Slow 3G-like']];
async function runWhatIf() {
  const url = $('wi-url').value.trim() || 'https://example.com';
  $('wi-out').innerHTML = '<article class="card full"><p class="fine">Predicting…</p></article>';
  const res = [];
  for (const [prof] of NETS) res.push(await ALO_API.predict(url, lastSys, predictOpts(prof)));
  $('wi-note').hidden = !ALO_API.mock;
  $('wi-note').textContent = ALO_API.mock ? 'API offline: these are sample numbers and do not vary by network.' : '';
  const max = Math.max(...res.map(r => r.baseline));
  $('wi-out').innerHTML = res.map((r, i) => `
    <article class="card">
      <div class="card-head"><h2>${NETS[i][1]}</h2></div>
      <div class="wi-nums"><div><small>Baseline</small><strong>${r.baseline.toFixed(1)} s</strong></div>
        <div><small>With ALO</small><strong class="good">${r.optimized.toFixed(1)} s</strong></div>
        <div><small>Saved</small><strong class="good">${MINUS}${(r.saved * 1000).toFixed(0)} ms</strong></div></div>
      <div class="bar wi-bar"><i style="width:${(r.baseline / max * 100).toFixed(0)}%; background:#cbd5e1"></i></div>
      <div class="bar wi-bar"><i style="width:${(r.optimized / max * 100).toFixed(0)}%"></i></div>
      <div class="tags" style="margin-top:12px">${methodTags(r)}</div>
    </article>`).join('') +
    `<article class="card full"><p class="fine">The same page is slower on slower networks, and the methods save more there because each removed
      kilobyte costs more time. ${res[0].source === 'dataset' ? '' : 'This URL is not in the training data, so a median page is assumed.'}</p></article>`;
}
$('wi-go').addEventListener('click', runWhatIf);
$('wi-url').addEventListener('keydown', e => { if (e.key === 'Enter') runWhatIf(); });

/* ---------------- optimizations page ---------------- */
function saveSettings() { store.set('alo_settings', settings); }
async function loadOptimizations() {
  const info = await ALO_API.getMethods();
  if (!info) { $('methods').innerHTML = '<p class="fine">API offline. Start the server to see the methods.</p>'; return; }
  document.querySelectorAll('input[name=mode]').forEach(r => { r.checked = r.value === settings.mode; });
  $('opt-min').value = settings.minMs;
  $('methods').innerHTML = info.methods.map(m => `
    <label class="method-row"><input type="checkbox" data-name="${esc(m.name)}" ${settings.disabled.includes(m.name) ? '' : 'checked'}>
      <div><b>${m.name}</b> <em class="tag method">${m.method}</em>
        <p>${esc(m.what)}</p>
        <p class="fine">Assumed to remove ${(m.cutFraction * 100).toFixed(0)}% of ${m.target} bytes from the first load. <b>Risk:</b> ${esc(m.risk)}</p></div></label>`).join('');
  $('formula').textContent = `${info.formula} (parallel discount ${info.parallelDiscount}).`;
  $('throughput').innerHTML = Object.entries(info.throughputMbps).map(([k, v]) => `<em class="tag method">${k.replace('_', ' ')}: ${v} Mbps</em>`).join('');
}
$('methods').addEventListener('change', e => {
  const n = e.target.dataset && e.target.dataset.name;
  if (!n) return;
  settings.disabled = e.target.checked ? settings.disabled.filter(x => x !== n) : [...settings.disabled, n];
  saveSettings();
});
document.querySelectorAll('input[name=mode]').forEach(r => r.addEventListener('change', () => { settings.mode = r.value; saveSettings(); }));
$('opt-min').addEventListener('input', e => { settings.minMs = Number(e.target.value) || 0; saveSettings(); });
$('opt-reset').addEventListener('click', () => { settings = { ...DEFAULT_SETTINGS }; saveSettings(); loadOptimizations(); });

/* ---------------- history page ---------------- */
function renderHistoryPage() {
  const q = $('h-filter').value.trim().toLowerCase();
  const rows = runs.filter(r => !q || r.url.toLowerCase().includes(q) ||
    r.actions.some(a => a.applied && (a.method || '').toLowerCase().includes(q)));
  $('h-body').innerHTML = rows.map(r => runRow(r, false)).join('');
  $('h-empty').hidden = rows.length > 0;
}
$('h-filter').addEventListener('input', renderHistoryPage);
$('h-clear').addEventListener('click', () => { runs = []; store.set('alo_history', runs); renderHistoryPage(); renderRecent(); });
$('h-csv').addEventListener('click', () => {
  const q = v => `"${String(v).replace(/"/g, '""')}"`;
  const lines = ['time,url,network,methods,saved_ms,baseline_s,predicted_s'].concat(runs.map(r =>
    [q(r.time), q(r.url), q(r.network), q(r.actions.filter(a => a.applied).map(a => a.method).join('+')),
      (r.saved * 1000).toFixed(0), r.baseline.toFixed(3), r.optimized.toFixed(3)].join(',')));
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
  a.download = 'alo_history.csv';
  a.click();
  URL.revokeObjectURL(a.href);
});

/* ---------------- model page ---------------- */
async function loadModel() {
  const m = await ALO_API.getModel();
  $('m-note').hidden = !!m;
  if (!m) { $('m-note').textContent = 'API offline. Start the server to see model details.'; return; }
  const k = m.metrics;
  $('m-stats').innerHTML = [
    ['Accuracy (R²)', k.r2_log.toFixed(2), 'on unseen sites'],
    ['Mean error', (k.mae_ms / 1000).toFixed(1) + ' s', 'per page'],
    ['Training runs', k.runs, 'page loads'],
    ['Websites', k.urls, 'in training data'],
  ].map(([l, v, s]) => `<div class="stat"><div class="stat-top"><span>${l}</span></div><div class="stat-val">${v}</div><small class="fine">${s}</small></div>`).join('');
  const maxE = Math.max(1, ...m.comparison.map(c => c.mae_ms));
  $('m-comp').innerHTML = m.comparison.length ? [...m.comparison].sort((a, b) => a.mae_ms - b.mae_ms).map(c =>
    `<div class="bar-row wide-label"><span>${esc(c.model)}</span><div class="bar"><i style="width:${(c.mae_ms / maxE * 100).toFixed(0)}%"></i></div><em>${(c.mae_ms / 1000).toFixed(1)} s</em></div>`).join('')
    : '<p class="fine">Run train_v2.py to generate the comparison.</p>';
  const maxI = Math.max(0.0001, ...m.importance.map(i => i.value));
  $('m-imp').innerHTML = m.importance.map(i =>
    `<div class="bar-row wide-label"><span>${esc(i.feature)}</span><div class="bar"><i style="width:${(i.value / maxI * 100).toFixed(0)}%"></i></div><em>${i.value.toFixed(2)}</em></div>`).join('');
  $('m-imp-note').textContent = (m.importance_kind || '') + '. Higher = the model depends on it more.';
  const iv = m.interval, NAMES = { Slow_3G: 'Slow 3G', Fast_4G: 'Fast 4G', No_Throttle: 'No throttle' };
  $('m-trust').innerHTML = iv ? Object.entries(iv.table).map(([net, rows]) => {
    // multiplier of the predicted time between which the real time fell 80% of the time (middle speed bucket as the example)
    const [lo, hi] = rows[1].map(v => Math.exp(v));
    const cov = iv.coverage_crossfit[net];
    return `<div class="bar-row wide-label"><span>${NAMES[net] || net} · ×${lo.toFixed(2)}–×${hi.toFixed(2)}</span>` +
      `<div class="bar"><i style="width:${(cov * 100).toFixed(0)}%"></i></div><em>${(cov * 100).toFixed(0)}%</em></div>`;
  }).join('') : '<p class="fine">Prediction ranges need a retrained model (python train_model.py).</p>';
  const ex = iv && (() => { const [a, b] = iv.table.Fast_4G[1], z = Math.log1p(4000); return [Math.expm1(z + a) / 1000, Math.expm1(z + b) / 1000]; })();
  $('m-trust-note').textContent = iv ? 'Bars show how often the real load time fell inside the range on websites the model never saw ' +
    '(target 80%). The multipliers are the range around a prediction of 3-8 s; faster and slower predictions use their own bands on Slow 3G and Fast 4G. ' +
    `Example: a 4 s prediction on Fast 4G means "probably between about ${ex[0].toFixed(1)} and ${ex[1].toFixed(1)} s". ` +
    'Pages predicted at 8 s or more on an unthrottled link are the weakest case.' : '';
  $('m-rules').textContent = m.rules;
}

/* ---------------- polling & wiring ---------------- */
function setConn() {
  $('conn-text').textContent = ALO_API.mock ? 'Mock data (API offline)' : 'Live (API connected)';
  $('conn-dot').classList.toggle('live', !ALO_API.mock);
}

async function tickSystem() {
  const s = await ALO_API.getSystem();
  lastSys = s;
  lastSysFull = s;
  lastCores = s.cores || [];
  for (const k of ['cpu', 'ram', 'rtt', 'bat']) push(series[k], s[k] ?? null);
  push(series.down, s.net_down_kbps ?? null);
  push(series.up, s.net_up_kbps ?? null);
  setConn();
  renderStats();
  if (!document.querySelector('.page[data-page=live]').hidden) renderLive();
}

async function runPrediction() {
  const p = await ALO_API.predict($('url').value.trim() || 'https://example.com', lastSys, predictOpts());
  push(trend.pred, p.optimized);
  push(trend.act, p.actual);
  setConn();
  recordRun(p);
  renderPrediction(p);
  renderTrend();
  renderRecent();
}

$('predict').addEventListener('click', runPrediction);
$('url').addEventListener('keydown', e => { if (e.key === 'Enter') runPrediction(); });

(async () => {
  route();
  for (let i = 0; i < HISTORY_LEN; i++) await tickSystem();
  if (runs.length === 0 || ALO_API.mock) for (let i = 0; i < (ALO_API.mock ? 6 : 1); i++) await runPrediction();
  else { const p = runs[0]; renderPrediction(p); trend.pred = runs.slice(0, 20).reverse().map(r => r.optimized); trend.act = runs.slice(0, 20).reverse().map(r => r.actual); renderTrend(); renderRecent(); }
  setInterval(tickSystem, 1500);
  // pages optimised in this browser by the ALO extension arrive here with their measured load time
  let lastLive = store.get('alo_last_live', null);
  setInterval(async () => {
    if (ALO_API.mock) return;
    try {
      const r = await fetch('/api/live' + (lastLive ? '?after=' + encodeURIComponent(lastLive) : ''));
      const fresh = (await r.json()).runs.filter(x => !runs.some(o => o.id === x.id));
      if (!fresh.length) return;
      fresh.forEach(x => runs.unshift(x));
      runs = runs.slice(0, 100);
      lastLive = fresh[fresh.length - 1].id;
      store.set('alo_last_live', lastLive);
      store.set('alo_history', runs);
      push(trend.pred, fresh[fresh.length - 1].optimized); push(trend.act, fresh[fresh.length - 1].actual);
      renderPrediction(fresh[fresh.length - 1]); renderTrend(); renderRecent();
    } catch { /* API offline */ }
  }, 3000);
  setInterval(() => { if (ALO_API.mock) runPrediction(); }, 6000);  // live mode predicts on demand only
})();
