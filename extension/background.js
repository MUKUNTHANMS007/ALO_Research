const API = 'http://localhost:8000';
const METHOD_OF = { lazy_images: 'Lazy-load images', deferred_scripts: 'Defer third-party scripts',
                    media_preload: 'Compress / defer media', fonts_preconnect: 'Limit web fonts' };

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg.type !== 'page-loaded') return;
  const tabId = sender.tab && sender.tab.id;
  // methods the extension did not actually apply on this page are marked disabled so the prediction matches reality
  const disabled = Object.entries(METHOD_OF).filter(([k]) => !(msg.stats[k] > 0)).map(([, v]) => v);
  fetch(`${API}/api/report`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ url: msg.url, load_ms: msg.load_ms, stats: msg.stats, disabled, page: msg.page }) })
    .then(r => r.json())
    .then(run => {
      chrome.storage.session.set({ ['tab' + tabId]: { url: msg.url, load_ms: msg.load_ms, stats: msg.stats, run } });
      if (tabId != null) {
        chrome.action.setBadgeBackgroundColor({ tabId, color: '#16a34a' });
        chrome.action.setBadgeText({ tabId, text: run.saved > 0 ? '-' + Math.round(run.saved * 1000) : '' });
      }
    })
    .catch(() => {
      if (tabId != null) { chrome.action.setBadgeBackgroundColor({ tabId, color: '#dc2626' }); chrome.action.setBadgeText({ tabId, text: '!' }); }
    });
});
