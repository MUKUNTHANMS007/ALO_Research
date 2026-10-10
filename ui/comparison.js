const $ = id => document.getElementById(id);
let allRows = [];
let sourceName = 'extension_comparison_results.csv';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));
const number = value => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};
const seconds = value => value == null ? '—' : `${(value / 1000).toFixed(2)} s`;
const bytes = value => {
  if (value == null) return '—';
  const abs = Math.abs(value);
  const sign = value < 0 ? '−' : '';
  return `${sign}${abs >= 1024 * 1024 ? (abs / 1024 / 1024).toFixed(2) + ' MB' : (abs / 1024).toFixed(1) + ' KB'}`;
};
function setMessage(text, kind = '') {
  const el = $('message');
  el.textContent = text;
  el.className = `message ${kind}`;
  el.hidden = !text;
}
function goodRows() {
  return allRows.filter(r => number(r.baseline_load_ms) != null && number(r.extension_load_ms) != null);
}
function renderStats() {
  const rows = goodRows();
  const deltas = rows.map(r => number(r.load_saved_ms)).filter(v => v != null);
  const baseline = rows.map(r => number(r.baseline_load_ms)).filter(v => v != null);
  const optimized = rows.map(r => number(r.extension_load_ms)).filter(v => v != null);
  const average = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  const faster = deltas.filter(v => v > 0).length;
  const cards = [
    ['Sites compared', `${rows.length}`],
    ['Average baseline load', seconds(average(baseline))],
    ['Average with ALO', seconds(average(optimized))],
    ['Average time saved', seconds(average(deltas))],
    ['Sites faster with ALO', `${faster} / ${rows.length}`],
  ];
  $('stats').innerHTML = cards.map(([label, value]) =>
    `<article class="stat"><div class="stat-top">${esc(label)}</div><div class="stat-val">${esc(value)}</div></article>`
  ).join('');
}
function renderTable() {
  const query = $('search').value.trim().toLowerCase();
  const rows = allRows.filter(r => String(r.url || '').toLowerCase().includes(query));
  rows.sort((a, b) => (number(b.load_saved_ms) ?? -Infinity) - (number(a.load_saved_ms) ?? -Infinity));
  $('row-count').textContent = `${rows.length} of ${allRows.length} sites · sorted by time saved`;
  $('results').innerHTML = rows.map(r => {
    const baseline = number(r.baseline_load_ms);
    const alo = number(r.extension_load_ms);
    const saved = number(r.load_saved_ms);
    const pct = number(r.load_saved_pct);
    const href = /^https?:\/\//i.test(r.url || '') ? r.url : '#';
    const savedClass = saved == null ? '' : saved >= 0 ? 'saved-positive' : 'saved-negative';
    return `<tr>
      <td title="${esc(r.url)}"><a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(r.url || 'Unknown site')}</a></td>
      <td>${seconds(baseline)}</td><td>${seconds(alo)}</td>
      <td class="${savedClass}">${seconds(saved)}</td>
      <td class="${savedClass}">${pct == null ? '—' : `${pct.toFixed(1)}%`}</td>
      <td>${bytes(number(r.bytes_saved))}</td>
    </tr>`;
  }).join('');
  if (!rows.length) {
    $('results').innerHTML = `<tr><td colspan="6">${allRows.length ? 'No sites match this filter.' : 'No comparison rows to display yet.'}</td></tr>`;
  }
}
function showRows(rows, filename) {
  allRows = rows;
  sourceName = filename || sourceName;
  renderStats();
  renderTable();
}
async function refresh() {
  setMessage('Loading comparison results…');
  try {
    const response = await fetch('/api/comparison/results', { cache: 'no-store' });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    const data = await response.json();
    showRows(data.rows || [], data.filename);
    if (!data.exists) setMessage('No results CSV found yet. Run extension_comparison.py, or open a CSV file below.', '');
    else setMessage(`Loaded ${allRows.length} rows from ${sourceName}.`, 'success');
  } catch (error) {
    setMessage(`Could not load results from the ALO server (${error.message}). Use “Open CSV” to choose the results file.`, 'error');
    showRows([], sourceName);
  }
}
function parseCsv(text) {
  const records = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); records.push(row); row = []; field = '';
    } else field += c;
  }
  if (field || row.length) { row.push(field); records.push(row); }
  if (!records.length) return [];
  const headers = records.shift();
  return records.filter(r => r.some(cell => cell !== '')).map(values =>
    Object.fromEntries(headers.map((key, i) => [key, values[i] ?? '']))
  );
}
$('refresh').addEventListener('click', refresh);
$('search').addEventListener('input', renderTable);
$('csv-file').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    showRows(parseCsv(await file.text()), file.name);
    setMessage(`Loaded ${allRows.length} rows from ${file.name}.`, 'success');
  } catch (error) {
    setMessage(`Could not read this CSV: ${error.message}`, 'error');
  }
});
refresh();
