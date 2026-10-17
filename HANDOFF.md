# ALO (Adaptive Latency Optimization): project handoff

Self-contained summary for someone (or an LLM session) with no prior context. `README.md` has the full operating
notes and `ALO_Training_Report.md` the detailed results; read this file first, then those.
Written 2026-10-15, updated 2026-10-17 after the back-to-back collection finished and the models were retrained.

## 1. What the project is for

ALO predicts how long a web page will take to load, given its size and the network it is loaded on, and estimates how much
load-reduction methods would save (lazy-load images, defer third-party scripts, compress media, limit fonts). A Chrome
extension applies some of them live and reports the measured load time back to the API; a dashboard shows predictions next
to measurements. An optional Windows tray agent samples system-wide network conditions (RTT, throughput, per-app
connection times) for an "OS Agent" dashboard page.

It is a working prototype by one person on one Windows machine (Python 3.12, git repo, branch `main`).

## 2. Repo map

| Part | Files |
|---|---|
| Data collector | `collect.py`, `urls.txt` (324 URLs). Playwright + headless Chromium. CDP throttling gives 3 networks: `No_Throttle`, `Fast_4G` (40 ms, 9 Mbit/s), `Slow_3G` (400 ms, 400 kbit/s). Records load time (`loadEventEnd`), bytes per resource type, CPU. "Variants" block a resource type (`no_images`, `no_fonts`, `no_media`, `no_third_party_scripts`) to measure its effect. |
| Training | `train_new.py` (evaluation only), `train_model.py` (exports models), `train_savings.py` (calibrates savings). Legacy: `train_all.py`, `train_v2.py`, `train_tree.py`, `clean.py`. |
| API + dashboard | `api/main.py` (FastAPI: `python -m uvicorn api.main:app --port 8000`), `ui/` (static dashboard served at `/`) |
| Browser extension | `extension/` (Chrome MV3: `content.js`, `background.js`, `defer_main.js`, `popup.*`) |
| OS agent | `agent/telemetry.py`, `agent/tray.py`, `agent/alo_agent.pyw`, `demo.py` |
| Models | `model/alo_et.joblib` (Extra Trees **predictor**), `model/alo_tree.joblib` (single tree, rule view only), `model/url_profiles.json` (page composition for 279 URLs), `model/savings_calibration.json`; backups in `model/v1_old/`, `model/v2_backup/` |

Data: `collected/runs.csv` (current; collector output), `collected/failed.csv`, `new_preprocessed.csv` (older 71-89 URL
dataset, now only used to fill in page profiles).

## 3. How the algorithm works

1. **Collect.** For each URL x network the collector loads the page and records load time, bytes per type (document,
   script, stylesheet, image, font, xhr_fetch, media, other), request count and DOM node count.
2. **Train.** Target = `log1p(page_load_time_ms)` (load times span 0.3-90 s). Inputs = 11 page-composition features +
   one-hot network profile. Evaluation = cross-validation **grouped by website** (5 folds x 3 repeats); a random split
   leaks because each site appears several times. Never use outcome columns (TTFB, request durations, task times) as
   inputs: that is leakage.
3. **Predict.** The API looks up the URL's stored page profile (exact, then hostname, else the global median page), or uses
   page bytes sent by the extension. Network profile is guessed from live RTT: <30 ms `No_Throttle`, <200 ms `Fast_4G`,
   else `Slow_3G`. The predictor returns a baseline load time.
4. **Savings per method.** *Lazy-load images* uses a **measured** effect: `baseline x slope[network] x image byte share x
   fraction of images cut`. The other three methods use an **estimate** `removed bytes x 8 / assumed throughput x 0.6`.
   Top 3 methods are applied; total saving capped at 70% of baseline. Each action carries `basis: "measured"|"estimate"`.
5. **Extension loop.** The extension applies the optimizations, measures load time and page weight (Resource Timing + DOM
   node count) and POSTs them to `/api/report`. With page bytes present, the model's prediction on the observed bytes is
   the "optimized" figure, so predicted vs measured is a like-for-like check of the model.

## 4. Results (website-grouped CV on the 967 runs / 279 sites the models are trained on)

| Model | R2 (log) | Mean error | Median error |
|---|---|---|---|
| Guess the median | n/a | ~12 s | n/a |
| Single tree, bytes + network (rule view only) | 0.698 | 5.0 s | 1.9 s |
| **Extra Trees, 11 features (current predictor)** | **0.745** | **4.4 s** | **1.6 s** |

- Extra Trees beat the tree in 13/15 paired folds (mean R2 gain +0.047, Wilcoxon p = 0.0003). Its `min_samples_leaf=2` was picked
  from {2, 4, 8} on an earlier CV of the same data, so the score is slightly optimistic.
- Before the 252 repeat-2 runs were added (715 runs / 277 sites) the same models scored 0.769 / 4.3 s and 0.717 / 5.2 s. The method is
  unchanged; why the new runs are harder to predict was not investigated.
- By network (tree -> Extra Trees, R2 / mean error): Slow 3G 0.91/4.0 s -> 0.92/3.1 s; Fast 4G 0.57/6.7 s -> 0.64/6.1 s;
  No_Throttle 0.44/3.9 s -> 0.53/3.6 s.
- Fast 4G is still the weakest: on the 715-run data its slowest 10% of loads (often 3-9 MB pages taking 50-90 s) averaged a 33 s error. Cause not found.
- Fast pages are over-predicted, slow ones under-predicted. Slow 3G predictions flatten near 83 s (90 s timeout). Failed loads
  were dropped, so the slowest pages are under-represented.

## 5. Findings from the analysis

1. **Old and new datasets disagree.** For 166 identical site+network pairs the bytes are identical but the old load times are
   1.8x longer (2.4x on Fast 4G). A model trained on one does not transfer to the other. The old collection script is not in
   the repo. Load time depends on the collection environment, not just bytes and network, so mapping real-world RTT onto
   the simulated profiles is risky.
2. **The "images save ~668 ms" claim was inflated by drift.** The default collector order is breadth-first, so a URL's baseline
   and `no_images` runs were a median of 71 h apart. Pages with zero image bytes still came out faster in the second run
   (39% Fast 4G, 8% No_Throttle, 2% Slow 3G).
3. **Back-to-back re-measurement (finished 2026-10-17, `collect.py --paired`, 253 pairs <1 h apart, alternating order).** The drift
   is gone (median "saving" 3% No_Throttle, 10% Fast 4G, 3% Slow 3G vs 22% / 48% / 10% before). A new **order effect** appeared:
   on the fast networks whichever run goes second is 14-19% faster whatever the variant (warm DNS/CDN/server caches); Slow 3G has none.
   Fitting `saving = a + slope x image share + c x order` gives the image slope: **Slow_3G 0.94 (90% CI 0.88-0.97, significant)**,
   Fast_4G 0.02 (-0.14..0.56) and No_Throttle 0.06 (-0.15..0.66), **both not distinguishable from zero**. So images are measurably
   costly only on bandwidth-bound links. Caveat: on Slow 3G 40% of baseline runs but only 26% of `no_images` runs timed out, and the
   lost sites are much heavier (5.7 MB vs 1.2 MB median), so that slope describes pages that load within 90 s.
4. **The byte-throughput savings estimate is unvalidated.** An early comparison called it ~15x too low for images, but that used the
   drift-inflated measurements, so no conclusion can be drawn. It is still used for scripts, fonts and media (`basis: "estimate"`).
5. **Browser byte counting is partial.** Chrome hides sizes of cross-origin responses without `Timing-Allow-Origin`; on 3 test
   pages Resource Timing disclosed 44-93% of true bytes. The API prices hidden requests at the median size of their type:
   within 3% of the true total on 2 pages, 65% too high on theguardian.com.
6. **No true held-out test set.** Evaluation is CV on the training runs only.

## 6. What was changed (as of 2026-10-17)

- Extension sends real page weight (`pageWeight()` in `extension/content.js`); API accepts optional `page` on `/api/predict`
  and `/api/report` (`source: "browser"`).
- New `train_savings.py` -> `model/savings_calibration.json` (image effect per network from adjacent baseline/`no_images` pairs, with the
  run-order term; median bytes per request by type). The API labels each action `basis: "measured"` (Slow 3G), `"measured-weak"`
  (images on fast links, effect not distinguishable from zero) or `"estimate"` (everything else).
- `train_model.py` also exports `alo_et.joblib`; the API predicts with Extra Trees (falls back to the tree if missing) and keeps the
  tree for the readable "decision path", labelled as a simplified view. The model page shows the current tree vs Extra Trees comparison.
- `collect.py --paired`: runs a URL's variants consecutively, alternating order, so drift cancels (`plan()`).
- README, `ALO_Training_Report.md`, `run_tomorrow.bat` updated.

**GitHub:** code, docs and models were pushed to origin/main on 2026-10-16 (merged with a collaborator's "extension comparison" page).
The retrained models, the final calibration and the full dataset (`collected/runs.csv`, `failed.csv`) were committed afterwards on
2026-10-17; local-only: `collected/runs_backup_before_paired.csv`, `model/v2_backup/`, `model/v3_backup/` (safety copies).

**Data (2026-10-17):** `runs.csv` has ~2,350 rows. The models are trained on 967 baseline runs from 279 sites (715 repeat 1 + 252 repeat 2).

## 7. Open items

1. **Test the extension in real Chrome.** It has never been run in a live session. Restart the API, reload the extension in
   `chrome://extensions`, browse some sites, check the badge and the Live runs page.
2. **Measure the other methods** (script deferral, fonts, media): only ~3 sites of data; still unvalidated byte-throughput estimates.
   Use `collect.py --paired` with alternating order (the order effect matters on fast links).
3. **Images on fast links:** is the effect truly ~0 or just too small to detect (median image share is only 13% of bytes)? More pairs,
   or pages selected for high image share, would tell.
4. Investigate the Fast 4G tail and why the repeat-2 runs are harder to predict; explain the old-vs-new dataset gap (needs the
   original collection script).
5. Unseen URLs get a global-median profile unless the caller sends `page` bytes (per-site size varies from 146 KB at the 10th
   percentile to 8.2 MB at the 90th). Network profile from RTT is crude. `_apply()` in `api/main.py` is dead code.
6. Operational: the collection lost ~15 h to laptop sleep / Wi-Fi drops. For long runs set Windows to never sleep while plugged in.

## 8. Gotchas

- `python train_model.py` overwrites the files in `model/`; back them up first, and restart the API afterwards.
- A harmless NumPy/SciPy version warning appears in this environment.
- If the API is unreachable, the dashboard silently falls back to **mock data**: check the bottom-left badge says "Live (API connected)".
- The OS agent only observes and recommends; it does not change other applications' traffic. Only Windows has been tried.
