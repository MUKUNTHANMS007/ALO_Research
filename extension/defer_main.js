// Runs in the page's own JS world. Delays scripts from known analytics/ads hosts that the page adds
// dynamically, until the page has loaded (or the user interacts). Parser-inserted <script> tags are left alone.
(() => {
  const TRACKERS = ['google-analytics.com', 'googletagmanager.com', 'doubleclick.net', 'googlesyndication.com',
    'googleadservices.com', 'connect.facebook.net', 'hotjar.com', 'clarity.ms', 'segment.com', 'segment.io',
    'mixpanel.com', 'amplitude.com', 'fullstory.com', 'optimizely.com', 'scorecardresearch.com', 'quantserve.com',
    'taboola.com', 'outbrain.com', 'criteo.com', 'adnxs.com', 'static.ads-twitter.com'];
  const isTracker = u => { try { const h = new URL(u, location.href).href; return TRACKERS.some(t => h.includes(t)); } catch { return false; } };
  const enabled = () => document.documentElement.dataset.aloDefer === '1';
  let released = false;
  const queue = [];
  const release = () => { if (released) return; released = true; queue.splice(0).forEach(f => { try { f(); } catch { /* ignore */ } }); };
  addEventListener('load', () => setTimeout(release, 1500), { once: true });
  ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach(ev => addEventListener(ev, release, { once: true, passive: true, capture: true }));
  setTimeout(release, 8000);  // safety net: never hold a script longer than 8 s

  const wrap = name => {
    const orig = Node.prototype[name];
    Node.prototype[name] = function (...args) {
      const n = args[0];
      if (!released && enabled() && n && n.tagName === 'SCRIPT' && n.src && isTracker(n.src)) {
        document.dispatchEvent(new CustomEvent('alo-deferred'));
        const self = this;
        queue.push(() => orig.apply(self, args));
        return n;
      }
      return orig.apply(this, args);
    };
  };
  wrap('appendChild');
  wrap('insertBefore');
})();
