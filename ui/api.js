// Data layer. Everything the UI needs comes through these two functions, so connecting the
// real OS agent and model later only means replacing the bodies below (e.g. fetch('/api/...')).
const ALO_API = (() => {
  const API_BASE = location.protocol === 'file:' ? 'http://localhost:8000' : '';
  const rnd = (a, b) => a + Math.random() * (b - a);
  const state = { cpu: 35, ram: 58, rtt: 60, bat: 82 };
  const walk = (v, step, lo, hi) => Math.min(hi, Math.max(lo, v + rnd(-step, step)));
  const NETS = [
    { name: 'Wi-Fi / Fast 4G', mult: 1 },
    { name: 'Slow 3G', mult: 3.4 },
    { name: 'No throttle', mult: 0.8 },
  ];

  // GET /api/system  -> live device + network state from the OS agent
  async function getSystem() {
    state.cpu = walk(state.cpu, 9, 5, 98);
    state.ram = walk(state.ram, 2, 30, 92);
    state.rtt = walk(state.rtt, 14, 15, 400);
    state.bat = walk(state.bat, 0.2, 5, 100);
    return { ...state, network: NETS[0].name };
  }

  // POST /api/predict -> model output for the given URL under the current conditions
  async function predict(url, sys) {
    const load = 1 + sys.cpu / 70;
    const net = 1 + sys.rtt / 120;
    const base = Math.max(0.6, rnd(1.4, 2.4) * load * net * (url.length % 7 + 3) / 4);
    const actions = [
      { name: 'Lazy-load images', detail: 'Defer offscreen images', method: 'Lazy loading', frac: rnd(0.12, 0.2) },
      { name: 'Defer third-party scripts', detail: 'Load analytics after interaction', method: 'Deferral', frac: rnd(0.08, 0.16) },
      { name: 'Limit web fonts', detail: 'Subset fonts, preload key ones', method: 'Subsetting', frac: rnd(0.03, 0.07) },
      { name: 'Compress media', detail: 'Serve smaller video/poster', method: 'Compression', frac: rnd(0.02, 0.06) },
    ].sort((a, b) => b.frac - a.frac).map((a, i) => ({ ...a, savedSec: base * a.frac, savedMs: base * a.frac * 1000, applied: i < 3 }));
    const saved = actions.slice(0, 3).reduce((s, a) => s + a.savedSec, 0);
    const total = rnd(800, 2400) * 1000;
    const parts = { Script: 0.38, Image: 0.27, Font: 0.1, Stylesheet: 0.08, Document: 0.05, Media: 0.09, Other: 0.03 };
    return {
      url, baseline: base, optimized: base - saved, saved, actions, network: NETS[0].name,
      bytes: Object.entries(parts).map(([k, v]) => ({ type: k, bytes: total * v * rnd(0.8, 1.2) })),
      actual: (base - saved) * rnd(0.88, 1.14),
    };
  }
  // Real backend first; if it is unreachable, fall back to the mock functions above.
  const api = { mock: true };
  async function post(path, body) {
    const r = await fetch(API_BASE + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }
  api.getSystem = async () => {
    try { const r = await fetch(API_BASE + '/api/system'); if (!r.ok) throw 0; api.mock = false; return await r.json(); }
    catch { api.mock = true; return getSystem(); }
  };
  // opts: { profile, disabled: [names], min_saving_ms }
  api.predict = async (url, sys, opts = {}) => {
    try { const p = await post('/api/predict', { url, ...opts }); api.mock = false; return p; }
    catch { api.mock = true; return predict(url, sys); }
  };
  // Info pages need the real backend; they return null when it is offline.
  const getJson = async path => {
    try { const r = await fetch(API_BASE + path); if (!r.ok) throw 0; api.mock = false; return await r.json(); }
    catch { return null; }
  };
  api.getMethods = () => getJson('/api/methods');
  api.getModel = () => getJson('/api/model');
  return api;
})();
