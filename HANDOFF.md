# ALO (Adaptive Latency Optimization): project handoff

Self-contained summary for someone (or an LLM session) with no prior context. `README.md` has the full operating
notes and `ALO_Training_Report.md` the detailed results; read this file first, then those.
Written 2026-10-15 from the state of the working tree on that date.

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
| Models | `model/alo_et.joblib` (Extra Trees **predictor**), `model/alo_tree.joblib` (single tree, rule view only), `model/url_profiles.json` (page composition for 277 URLs), `model/savings_calibration.json`; backups in `model/v1_old/`, `model/v2_backup/` |

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

## 4. Results (website-grouped CV, on the 715 runs / 277 sites the models were trained on)

| Model | R2 (log) | Mean error | Median error |
|---|---|---|---|
| Guess the median | n/a | 12.1 s | n/a |
| Single tree, bytes + network (previous predictor) | 0.717 | 5.16 s | 1.95 s |
| **Extra Trees, 11 features (current predictor)** | **0.769** | **4.34 s** | **1.58 s** |

- Extra Trees beat the tree in 15/15 paired folds (Wilcoxon p = 0.0001). Its `min_samples_leaf=2` was picked from {2, 4, 8}
  on the same CV, so the score is slightly optimistic.
- By network (tree -> Extra Trees, R2 / mean error): Slow 3G 0.90/4.4 s -> 0.92/3.4 s; Fast 4G 0.58/7.6 s -> 0.65/6.5 s;
  No_Throttle 0.44/3.3 s -> 0.55/2.9 s.
- Fast 4G is still the weakest: its slowest 10% of loads (often 3-9 MB pages taking 50-90 s) average a 33 s error. Cause not found.
- Fast pages are over-predicted, slow ones under-predicted. Slow 3G predictions flatten near 83 s (90 s timeout). Failed loads
  were dropped, so the slowest pages are under-represented.

## 5. Findings from the analysis

1. **Old and new datasets disagree.** For 166 identical site+network pairs the bytes are identical but the old load times are
   1.8x longer (2.4x on Fast 4G). A model trained on one does not transfer to the other. The old collection script is not in
   the repo. Load time depends on the collection environment, not just bytes and network, so mapping real-world RTT onto
   the simulated profiles is risky.
2. **The "images save ~668 ms" claim was inflated by drift.** The default collector order is breadth-first, so a URL's baseline
   and `no_images` runs were a median of 71 h apart. Pages with zero image bytes still came out faster in the second run
   (39% Fast 4G, 8% No_Throttle, 2% Slow 3G). `train_savings.py` fits `saving = offset + slope x image share` per network and
   keeps the slope: Slow_3G 0.95 (90% CI 0.91-0.97), Fast_4G 0.50 (0.40-0.61), No_Throttle 0.30 (0.17-0.46).
3. **Back-to-back data confirms the diagnosis (2026-10-14).** 46 adjacent No_Throttle pairs (runs seconds apart) show a median
   image saving of 4.4% (the same URLs, 71 h apart, showed 32.6%), drift offset about 0 (-0.03) and slope 0.33, in line with
   the calibrated 0.30. Small sample (46 sites, 6 zero-image controls); the Fast_4G and Slow_3G adjacent data do not exist yet.
4. **The original per-method savings estimate was far too low** (median ~47 ms vs ~727 ms measured, though the measured
   figure itself included drift).
5. **Browser byte counting is partial.** Chrome hides sizes of cross-origin responses without `Timing-Allow-Origin`; on 3 test
   pages Resource Timing disclosed 44-93% of true bytes. The API prices hidden requests at the median size of their type:
   within 3% of the true total on 2 pages, 65% too high on theguardian.com.
6. **No true held-out test set.** Evaluation is CV on the training runs only.

## 6. What was changed (as of 2026-10-15)

- Extension sends real page weight (`pageWeight()` in `extension/content.js`); API accepts optional `page` on `/api/predict`
  and `/api/report` (`source: "browser"`).
- New `train_savings.py` -> `model/savings_calibration.json` (measured image effect per network; median bytes per request by type).
  It switches to back-to-back pairs automatically once 100 sites have them.
- `train_model.py` also exports `alo_et.joblib`; the API predicts with Extra Trees (falls back to the tree if missing) and keeps the
  tree for the readable "decision path", labelled as a simplified view. The model page shows the current tree vs Extra Trees comparison.
- `collect.py --paired`: runs a URL's variants consecutively, alternating order, so drift cancels (`plan()`).
- README, `ALO_Training_Report.md`, `run_tomorrow.bat` updated.

**Pushed to GitHub (origin/main) on 2026-10-16:** all code, docs and the new models (`model/alo_et.joblib`,
`model/savings_calibration.json`). **Not committed:** `collected/runs.csv` and `collected/failed.csv` (the paired collection was
still appending to them; commit them once it finishes, since `runs.csv` is the only copy of the dataset), the local safety copy
`collected/runs_backup_before_paired.csv`, and `model/v2_backup/` (duplicates of earlier model files).

**Data changed after the models were trained.** `collected/runs.csv` grew from 1,763 to 1,865 rows and `failed.csv` by 18 rows
on 2026-10-14 (about 11:05-11:20 local): repeat-2 `baseline` + `no_images` runs for 51 URLs, **No_Throttle only**, apparently
from a `collect.py --paired` run that was started and stopped early (not by the assistant). Usable baseline runs are now 762 /
278 sites, but the exported models, the metrics and the README numbers (715 runs / 277 sites) still describe the earlier data.
Re-run `train_model.py` and `train_savings.py` to bring them up to date (back up `model/` first).

## 7. Open items

1. **Finish the back-to-back collection.** Remaining: URLs beyond the first ~51 on No_Throttle, plus all of Fast_4G and Slow_3G.
   ```
   python collect.py --paired --variants baseline,no_images --cpus cpu_1x --caches cold --repeats 2 --limit 120
   ```
   It resumes (finished runs are skipped). About 720 runs in total, roughly 3-6 h; keep the laptop plugged in, awake and online.
   Then `python train_savings.py` for clean slopes on the fast networks.
2. **Retrain** on the current `runs.csv` (see above).
3. **Test the extension in real Chrome.** It has never been run in a live session. Restart the API, reload the extension in
   `chrome://extensions`, browse some sites, check the badge and the Live runs page.
4. **Measure the other methods** (script deferral, fonts, media): only ~3 sites of data; still byte-throughput estimates.
5. Investigate the Fast 4G tail; explain the old-vs-new dataset gap (probably needs the original collection script).
6. Unseen URLs get a global-median profile unless the caller sends `page` bytes (per-site size varies from 146 KB at the 10th
   percentile to 8.2 MB at the 90th). Network profile from RTT is crude. `_apply()` in `api/main.py` is dead code.

## 8. Gotchas

- `python train_model.py` overwrites the files in `model/`; back them up first, and restart the API afterwards.
- A harmless NumPy/SciPy version warning appears in this environment.
- If the API is unreachable, the dashboard silently falls back to **mock data**: check the bottom-left badge says "Live (API connected)".
- The OS agent only observes and recommends; it does not change other applications' traffic. Only Windows has been tried.
