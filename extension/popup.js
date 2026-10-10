const KEYS = ['enabled', 'lazy', 'defer', 'media', 'fonts'];
const DEFAULTS = { enabled: true, lazy: true, defer: true, media: true, fonts: true };
(async () => {
  const cfg = { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) };
  KEYS.forEach(k => {
    const el = document.getElementById(k); el.checked = cfg[k];
    el.addEventListener('change', () => chrome.storage.local.set({ [k]: el.checked }));
  });
  try {
    const h = await (await fetch('http://localhost:8000/api/health')).json();
    document.getElementById('api').innerHTML = `<span class="ok">&#9679; API connected</span> (model R² ${h.model.r2_log.toFixed(2)})`;
  } catch {
    document.getElementById('api').innerHTML = '<span class="bad">&#9679; API not running</span> - start start_alo.bat';
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const d = (await chrome.storage.session.get('tab' + tab.id))['tab' + tab.id];
  if (d) {
    const s = d.stats;
    document.getElementById('page').innerHTML =
      `<b>${(d.load_ms / 1000).toFixed(2)} s</b> measured load<br>` +
      `Predicted with ALO: ${d.run.optimized.toFixed(2)} s (baseline ${d.run.baseline.toFixed(2)} s)<br>` +
      `Applied: ${s.lazy_images} lazy images, ${s.deferred_scripts} delayed scripts, ${s.media_preload} media, ${s.fonts_preconnect} font hints`;
  }
})();
