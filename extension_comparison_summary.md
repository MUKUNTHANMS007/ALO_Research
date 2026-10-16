# ALO extension comparison

Generated: 2026-10-10 08:53 UTC  
Sites included: 25; sites skipped for errors or exceeding the load limit: 4; order seed: 42

Each site was visited once in a fresh Chromium profile with the unpacked ALO extension and once without it.
Positive savings means the extension run was faster. This is a single-run comparison; timings vary with network and site state.

## Summary

| Measure | Result |
|---|---:|
| Sites faster with ALO | 15/25 |
| Mean load time saved | 307 ms |
| Median load time saved | 148 ms |
| Mean percentage saved | 3.9% |
| Mean transferred bytes saved | 479.6 KB |

## Per-site results

| Site | Baseline load | ALO load | Saved | Saved % | Bytes saved | Status |
|---|---:|---:|---:|---:|---:|---|
| www.bbc.com | 3,803 ms | 2,839 ms | 964 ms | 25.4% | 1,040.8 KB | paired |
| www.reuters.com | 1,361 ms | 720 ms | 641 ms | 47.1% | 0.0 KB | paired |
| www.theguardian.com | 1,248 ms | 1,214 ms | 35 ms | 2.8% | 12.5 KB | paired |
| www.npr.org | 2,151 ms | 1,456 ms | 694 ms | 32.3% | 254.5 KB | paired |
| www.wikipedia.org | 837 ms | 1,149 ms | -312 ms | -37.3% | 0.0 KB | paired |
| www.github.com | 1,726 ms | 1,428 ms | 298 ms | 17.3% | 1,056.3 KB | paired |
| news.ycombinator.com | 1,932 ms | 1,547 ms | 385 ms | 19.9% | 0.0 KB | paired |
| www.mozilla.org | 4,158 ms | 4,204 ms | -46 ms | -1.1% | 505.9 KB | paired |
| www.python.org | 917 ms | 968 ms | -51 ms | -5.6% | 0.0 KB | paired |
| www.microsoft.com | 5,556 ms | 5,158 ms | 398 ms | 7.2% | 91.1 KB | paired |
| www.apple.com | 1,605 ms | 1,077 ms | 528 ms | 32.9% | 0.0 KB | paired |
| www.amazon.com | 516 ms | 712 ms | -196 ms | -38.0% | 0.0 KB | paired |
| www.ebay.com | 2,620 ms | 2,472 ms | 148 ms | 5.6% | 172.3 KB | paired |
| www.etsy.com | 1,291 ms | 2,436 ms | -1,145 ms | -88.7% | 0.0 KB | paired |
| www.bestbuy.com | 1,230 ms | 1,703 ms | -473 ms | -38.5% | 172.6 KB | paired |
| www.nytimes.com | 4,670 ms | 2,948 ms | 1,722 ms | 36.9% | 7,245.6 KB | paired |
| www.medium.com | 1,836 ms | 1,754 ms | 81 ms | 4.4% | 0.0 KB | paired |
| dev.to | 1,593 ms | 1,676 ms | -83 ms | -5.2% | 164.1 KB | paired |
| www.smashingmagazine.com | 2,184 ms | 1,188 ms | 996 ms | 45.6% | -76.4 KB | paired |
| www.nationalgeographic.com | 1,767 ms | 3,295 ms | -1,528 ms | -86.5% | 212.7 KB | paired |
| www.canva.com | 3,085 ms | 2,723 ms | 362 ms | 11.7% | 267.4 KB | paired |
| www.dropbox.com | 3,879 ms | 4,578 ms | -699 ms | -18.0% | 0.0 KB | paired |
| www.cloudflare.com | 4,526 ms | 1,008 ms | 3,517 ms | 77.7% | 37.6 KB | paired |
| www.mozilla.org/en-US/firefox/ | 2,862 ms | 1,129 ms | 1,733 ms | 60.6% | 466.8 KB | paired |
| www.espn.com | 2,758 ms | 3,040 ms | -282 ms | -10.2% | 366.5 KB | paired |

## Measurement notes

- The extension is loaded from the `extension/` folder using Chromium's unpacked-extension flags.
- Fresh profiles keep cookies and cache from one condition out of the other.
- Requests and transferred bytes are estimated from response Content-Length (or response bodies when available).
- The comparison measures the extension's actual page behavior; it does not simulate or force-block resources.
- Compare FCP/LCP alongside full load time. A single visit per condition is directional, not a controlled benchmark.
