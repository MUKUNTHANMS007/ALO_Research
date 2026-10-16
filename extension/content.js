// Applies page-level optimizations and reports the measured load time. Everything is best effort and reversible
// (just reload with the extension off).
(async () => {
  const DEFAULTS = { enabled: true, lazy: true, defer: true, media: true, fonts: true };
  let cfg = DEFAULTS;
  try { cfg = { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) }; } catch { /* use defaults */ }
  if (!cfg.enabled || window.top !== window) return;

  const stats = { lazy_images: 0, deferred_scripts: 0, media_preload: 0, fonts_preconnect: 0 };
  if (cfg.defer) {
    document.documentElement.dataset.aloDefer = '1';
    document.addEventListener('alo-deferred', () => { stats.deferred_scripts++; });
  }

  const seen = new WeakSet();
  const fonts = { done: false };
  function handle(el) {
    if (!el || seen.has(el)) return;
    const tag = el.tagName;
    if (tag === 'IMG' && cfg.lazy) {
      seen.add(el);
      // leave explicit priority hints and already-eager images alone (likely the main/LCP image)
      if (!el.hasAttribute('loading') && el.getAttribute('fetchpriority') !== 'high' && !el.complete) {
        el.loading = 'lazy'; el.decoding = 'async'; stats.lazy_images++;
      }
    } else if (tag === 'IFRAME' && cfg.lazy) {
      seen.add(el);
      if (!el.hasAttribute('loading')) { el.loading = 'lazy'; stats.lazy_images++; }
    } else if ((tag === 'VIDEO' || tag === 'AUDIO') && cfg.media) {
      seen.add(el);
      if (!el.autoplay && el.preload !== 'none') { el.preload = 'none'; stats.media_preload++; }
    } else if (tag === 'LINK' && cfg.fonts && !fonts.done && /fonts\.googleapis\.com/.test(el.href || '')) {
      fonts.done = true;
      const l = document.createElement('link');
      l.rel = 'preconnect'; l.href = 'https://fonts.gstatic.com'; l.crossOrigin = 'anonymous';
      (document.head || document.documentElement).appendChild(l); stats.fonts_preconnect++;
    }
  }
  const scan = root => {
    if (root.nodeType !== 1) return;
    handle(root);
    if (root.querySelectorAll) root.querySelectorAll('img,iframe,video,audio,link[href*="fonts.googleapis.com"]').forEach(handle);
  };
  new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(scan))).observe(document, { childList: true, subtree: true });

  // Page weight by the same resource types the model uses. Cross-origin responses without Timing-Allow-Origin report
  // transferSize 0 (the browser hides it): those are counted in `unknown` and priced by the API at a typical size.
  function typeOf(e) {
    const path = e.name.split(/[?#]/)[0].toLowerCase();
    if (/\.(woff2?|ttf|otf|eot)$/.test(path)) return 'font';
    if (/\.(png|jpe?g|gif|webp|avif|svg|ico|bmp)$/.test(path)) return 'image';
    if (/\.(mp4|webm|m3u8|mp3|ogg|wav|m4a|mpd)$/.test(path)) return 'media';
    if (/\.m?js$/.test(path)) return 'script';
    if (/\.css$/.test(path)) return 'stylesheet';
    switch (e.initiatorType) {
      case 'script': return 'script';
      case 'img': case 'image': return 'image';
      case 'video': case 'audio': return 'media';
      case 'xmlhttprequest': case 'fetch': return 'xhr_fetch';
      default: return 'other';
    }
  }
  function pageWeight(nav) {
    const bytes = { document: nav.transferSize || 0 }, unknown = {};
    const res = performance.getEntriesByType('resource');
    for (const e of res) {
      const t = typeOf(e);
      if (e.transferSize > 0) bytes[t] = (bytes[t] || 0) + e.transferSize;
      else if (e.encodedBodySize === 0 && e.deliveryType !== 'cache') unknown[t] = (unknown[t] || 0) + 1;
    }
    // Chrome's "Nodes" counter (what the collector recorded) counts every DOM node, text nodes included.
    let nodes = 0;
    const it = document.createNodeIterator(document, NodeFilter.SHOW_ALL);
    while (it.nextNode()) nodes++;
    return { bytes, unknown, requests: res.length + 1, dom_nodes: nodes };
  }

  addEventListener('load', () => setTimeout(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    if (!nav || !(nav.loadEventEnd > 0)) return;
    try {
      chrome.runtime.sendMessage({ type: 'page-loaded', url: location.origin + location.pathname, load_ms: nav.loadEventEnd,
                                   stats, page: pageWeight(nav) });
    } catch { /* extension reloaded */ }
  }, 800), { once: true });
})();
