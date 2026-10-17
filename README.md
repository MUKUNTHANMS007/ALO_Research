# ALO - Adaptive Latency Optimization

Predicts how long a web page takes to load from its size and the network it is loaded on, estimates what load-reduction
methods (lazy loading, script deferral, ...) would save, and applies some of them live in Chrome through an extension.
A dashboard shows predictions next to the load times measured in the browser.

> Status (2026-10-17): working prototype. Model trained on 967 runs / 279 websites. Hand-over notes are in section 9; a self-contained summary for newcomers is in `HANDOFF.md`.

## 1. What is in this repo

| Part | Files | What it does |
|---|---|---|
| Data collector | `collect.py`, `urls.txt` | Loads URLs in headless Chromium (Playwright) under simulated networks / CPU / cache / blocking variants and records timings, bytes, CPU. |
| Training / evaluation | `train_new.py`, `train_model.py`, `train_savings.py` | `train_new.py` evaluates models on `collected/runs.csv`; `train_model.py` exports the model the API uses; `train_savings.py` writes `model/savings_calibration.json` (measured image effect per network, used for the per-method savings). |
| Legacy training (first dataset) | `clean.py`, `train_tree.py`, `train_v2.py`, `train_all.py`, `new_preprocessed.csv`, `page_runs*.csv` | Original 71-site experiments. `train_all.py` produced `reports/results.json` and the `reports/fig_*.png` figures (old data only). |
| API + dashboard | `api/main.py`, `ui/` | FastAPI serves predictions at `/api/*` and the dashboard at `/`. |
| **OS agent** | `agent/telemetry.py`, `agent/tray.py`, `agent/alo_agent.pyw` | Background Windows agent with a tray On/Off switch. Measures the network of **every** application (no browser needed) and feeds the dashboard's *OS Agent* page. See section 4b. |
| Browser extension | `extension/` | Chrome MV3 extension: applies optimizations to pages you open and reports measured load time to the API. |
| Reports | `ALO_Training_Report.md` (current), `reports/ALO_Training_Report_v1_71sites.md` (old) | Results and limits. Read `ALO_Training_Report.md` first. |
| Saved models | `model/alo_et.joblib` (**predictor** used by the API: Extra Trees, 11 features), `model/alo_tree.joblib` (single tree, used only for the readable rules / decision path), `model/url_profiles.json`, `model/savings_calibration.json`, `model/alo_extratrees.joblib` (old, not wired in), `model/v1_old/`, `model/v2_backup/` (previous exports) | |
| Helpers | `start_alo.bat`, `run_tomorrow.bat` | Start the API; run the long data collection + retrain. |

## 2. Setup

Python 3.12 on Windows was used. From the project folder:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium     # only needed for collect.py
```

`scikit-learn`/`scipy` print a harmless NumPy-version warning in the original environment (NumPy 2.4 with SciPy 1.13).
If it bothers you, install `numpy<2.3`.

## 3. Run the dashboard + API

```
python -m uvicorn api.main:app --port 8000      # or double-click start_alo.bat
```

Open http://localhost:8000. The bottom-left of the dashboard says "Live (API connected)" when it is talking to the real
backend. If the API is unreachable the UI silently falls back to **mock data** (`ui/api.js`), so check that badge and that
the Model page shows 967 runs / 279 sites.

API endpoints (`api/main.py`):

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | model metrics |
| `GET /api/system` | live CPU/RAM/RTT of this machine and the network profile guessed from RTT |
| `POST /api/predict` `{url, profile?, disabled?, min_saving_ms?, page?}` | baseline prediction + per-method savings. `page` = `{bytes:{type:n}, unknown:{type:count}, requests}` is the real page weight seen by the browser; when present it replaces the stored/median profile (`source: "browser"`). |
| `POST /api/report` `{url, load_ms, stats, disabled, page?}` | called by the extension after a page load; stored with predicted vs measured. With `page`, the model's prediction on the observed bytes is the "optimized" figure, so predicted vs measured is a like-for-like check of the model. |
| `GET /api/live?after=<id>` | runs reported by the extension (the dashboard polls this) |
| `GET /api/methods`, `GET /api/model` | info pages |

## 4. Browser extension

1. Start the API (section 3).
2. Chrome -> `chrome://extensions` -> turn on Developer mode -> **Load unpacked** -> pick the `extension/` folder.
3. Browse. The badge shows `-NNN` (ms saved, predicted) or a red `!` when the API is not running. The popup has toggles per method.

What it applies (all best effort; see limits): lazy-load images/iframes, delay dynamically added analytics/ad scripts
(host list in `extension/defer_main.js`), `preload=none` on video/audio, a `preconnect` for Google Fonts.
Measured loads are appended to `collected/live_runs.jsonl`.

**The extension has not been tested in a real browser session yet** (only syntax checks and the API calls). Expect to debug it.
Known limits: Chrome's preload scanner may fetch images/scripts from raw HTML before the extension can change them, so
savings are partial; the extension measures only the optimized load, so "actual vs predicted" is not an A/B test.

## 4b. OS agent (system-wide, runs in the background)

ALO as a background service of the laptop instead of an app the user opens. It lives in the system tray
(`ALO enabled` switch, `Open dashboard`, `Start with Windows`, `Quit`). While enabled it samples every 5 s:

- system CPU/RAM, download/upload rate, round-trip time to a public host, and the network profile that implies;
- which applications hold open internet connections (from `psutil`, no admin needed) and the **real TCP connect time**
  to each application's servers (probed every 15 s, at most 12 probes).

```
pip install -r requirements.txt            # includes pystray + pillow
pythonw agentlo_agent.pyw                 # no console window; errors go to collected/agent_log.txt
python -m agent.tray                       # same, with a console (for debugging)
```

The agent starts the API/dashboard on port 8000 if nothing is serving there, so do not start uvicorn separately
(the API process hosts the sampler, `Telemetry` in `agent/telemetry.py`). Open http://localhost:8000/#agent (or the tray
icon) for the *OS Agent* page. The switch there and the tray switch are the same setting (`agent_state.json`);
OFF means nothing is sampled at all. Endpoints: `GET /api/agent/status`, `GET /api/agent/series`, `POST /api/agent/toggle`.

Output (for training on real data, git-ignored because it names this machine's applications):
`collected/os_telemetry.csv` (one row per sample: profile, RTT, CPU, RAM, throughput, number of apps/connections) and
`collected/os_apps.csv` (per application: connections, connect time). Privacy by design: only process names and aggregate
numbers are saved; no URLs, no remote IPs, no traffic content.

### Live demo

```
python demo.py                 # opens the dashboard + 4 websites, downloads a 10 MB test file, prints live readings for ~60 s
python demo.py --no-browser    # console only
```

It starts the agent if needed, shows the throughput spike and which applications are online, then flips the switch OFF to
show that sampling stops, and ON again. The download is from speed.cloudflare.com.

**What it does not do (yet):** it observes and recommends; it does not change other applications' traffic. Optimizing
traffic system-wide would need a local proxy (HTTPS would need a root certificate) or Windows network settings
changes with admin rights. Per-application *throughput* is not measured (Windows needs ETW/admin for that); the
agent uses connection counts and connect time instead. Only Windows has been tried; the registry autostart is Windows-only.

## 5. Data

| File | Rows | Notes |
|---|---|---|
| `collected/runs.csv` | ~2,350 | **Current training data.** Output of `collect.py`. Training uses only `variant=baseline`, `cpu_1x`, `cold` cache, success, >1 request, >100 ms: 967 runs (715 repeat 1 + 252 repeat 2), 279 sites. Other rows are the `no_images` variant (~1,100, of which ~250 are back-to-back pairs) and small samples of other variants/CPU/cache. |
| `collected/failed.csv` | ~400 | Failed runs (timeouts, resets); not used for training. |
| `new_preprocessed.csv` | 801 | First dataset (89 URLs x 3 networks x 3 repeats, collected 2026-09-26). Now only used for page profiles of URLs the new collector missed. |
| `page_runs.csv`, `page_runs_clean.csv`, `network_requests.csv` | | Raw inputs of the first dataset. `network_requests.csv` (46 MB) is git-ignored; ask the owner if you need it. The script that produced the first dataset is **not** in this repo. |
| `urls.txt` | 324 | URL list for the collector. |

Networks are simulated with Chrome DevTools throttling: `No_Throttle`, `Fast_4G` (40 ms, 9 Mbit/s), `Slow_3G` (400 ms, 400 kbit/s).

### Collecting more data

```
python collect.py --quick --repeats 1                       # baseline + no_images, cpu_1x, cold
python collect.py --variants no_third_party_scripts,no_fonts,no_media --cpus cpu_1x --caches cold --repeats 1
python collect.py --variants baseline --cpus cpu_1x --caches cold --repeats 2
```

- **Measuring a variant's effect (e.g. image blocking): use `--paired`.** The default order is breadth-first, which puts a URL's baseline and variant runs ~71 h apart and makes drift look like savings (section 8). `python collect.py --paired --variants baseline,no_images --cpus cpu_1x --caches cold --repeats 2 --limit 120` runs both variants of each URL + network back to back, alternating which goes first (`plan()` in `collect.py`). Repeat 1 is already in `runs.csv` (skipped), so this adds repeat 2. Roughly 120 sites x 3 networks x 2 variants = 720 runs, about 3 to 6 hours (median 7-8 s per run unthrottled/4G, 26 s on Slow 3G). `train_savings.py` switches to these adjacent pairs automatically once 100 sites have them.
- Resumable: finished combinations are skipped. It waits (and logs) when the internet is down, and never records offline failures as data.
- Slow. A full run of every combination is ~58,000 runs (days). Slow 3G pages take up to 90 s each and often time out.
- Keep the laptop plugged in, awake and online. `run_tomorrow.bat` runs the 2nd and 3rd commands, then `train_new.py` and `train_model.py`.
- All the machine's load affects timings: close other heavy apps while collecting, and say which machine collected what.

## 6. Training

```
python train_new.py       # evaluation only: prints R2/error for several models, per-network results
python train_model.py     # exports model/alo_tree.joblib + model/url_profiles.json (restart the API afterwards)
```

- Target: `log1p(page_load_time_ms)`. Evaluation: **cross-validation grouped by website** (5 folds, repeated 3 times); a site is never in both train and test. Never use a random split: each site appears several times and it leaks.
- Exported models (`train_model.py`): **Extra Trees** (300 trees, `min_samples_leaf=2`) on the 11 page-composition features + network profile, which does the predicting; and the single decision tree on `total_network_bytes` + network profile (more features did not help a *single* tree), kept because its rules are readable. On the same 15 grouped folds Extra Trees beat the tree in 13/15 (paired dR2 +0.047, Wilcoxon p = 0.0003; it was 15/15 on the earlier 715 runs). `min_samples_leaf` was chosen from {2, 4, 8} on that same CV, so its score is slightly optimistic.
- Do not use outcome columns measured during the load (request durations, TTFB, task time) as inputs: that is leakage and inflated R2 by 0.12 to 0.17 in the first study. TTFB gives R2 0.80 in `train_new.py` but is unusable for "predict before loading".
- `train_model.py` overwrites `model/alo_tree.joblib`. Copy the old one first (as `run_tomorrow.bat` does into `model/v2_backup/`).

## 7. Current results (grouped CV, unseen websites)

| Model | R2 (log) | Mean error |
|---|---|---|
| Median guess | n/a | 12.1 s |
| **Extra Trees, 11 features (exported predictor)** | **0.745 +/- 0.033** | 4.4 s (median 1.6 s) |
| Decision tree, bytes + network (exported rule view) | 0.698 +/- 0.027 | 5.0 s (median 1.9 s) |

These are on the 967-run dataset. On the earlier 715 runs the same models scored 0.769 / 4.3 s and 0.717 / 5.2 s; the method is unchanged, so the drop comes with the added repeat-2 runs (and the different mix of networks and sites in them); why they are harder to predict was not investigated. The older rows of this table (Extra Trees with `min_samples_leaf=4`, Extra Trees + TTFB at R2 0.80 which is not usable pre-load) were measured on the 715-run data and have not been repeated.

Per network (same folds, R2 / mean error): single tree Slow 3G 0.91 / 4.0 s, Fast 4G 0.57 / 6.7 s, No_Throttle 0.44 / 3.9 s; **Extra Trees** Slow 3G 0.92 / 3.1 s, Fast 4G 0.64 / 6.1 s, No_Throttle 0.53 / 3.6 s. Fast 4G stays the weakest: its slowest 10% of loads (often 3-9 MB pages that took 50-90 s) still average a 33 s error (measured on the 715-run data), and the cause is not investigated.
The model trained on the first dataset transfers badly to the new data (R2 0.48), so do not mix the datasets blindly.
Full details: `ALO_Training_Report.md`.

## 8. Measured effect of blocking images (`no_images` variant)

795 paired runs (same URL + network, baseline vs images blocked): median load time change -668 ms (Fast 4G -1,239 ms,
Slow 3G -587 ms, No_Throttle -396 ms). This is an upper bound (images are removed, not compressed). The other variants
(third-party scripts, fonts, media) have only 3 sites of data so far.

**Those raw medians are inflated by drift.** The collector runs breadth-first, so a URL's `no_images` run happened a median
of 71 h after its baseline run. Pages with no image bytes at all (the control group) still came out faster in the second
run: 39% on Fast 4G, 8% on No_Throttle, 2% on Slow 3G. `train_savings.py` therefore fits `saving = offset + slope x
image byte share` per network and keeps only the slope (90% CI from a bootstrap over websites). That first fit, on the
71 h-apart pairs, gave slopes of 0.95 (Slow 3G), 0.50 (Fast 4G) and 0.30 (No_Throttle), but with a 0.40 / 0.17 offset on the
fast networks, i.e. mostly drift.

**Back-to-back re-measurement (2026-10-17).** `collect.py --paired` collected 253 adjacent pairs (<1 h apart, alternating
which variant goes first; 95 No_Throttle, 96 Fast 4G, 62 Slow 3G). Two things showed up:

1. The drift disappears: the median "saving" falls to 3% (No_Throttle), 10% (Fast 4G) and 3% (Slow 3G), against 22% / 48% / 10% for the 71 h pairs.
2. **On the fast networks, whichever run goes second is 14-19% faster whatever the variant** (warm DNS / CDN / server caches).
   Slow 3G has no such effect (0.001). `train_savings.py` therefore fits `saving = a + slope x image share + c x (no_images ran second)` and keeps the slope:

| Network | Pairs | Slope | 90% CI | Order effect c | Verdict |
|---|---|---|---|---|---|
| Slow_3G | 62 | 0.94 | 0.88-0.97 | 0.00 | significant: removing images saves about their byte share |
| Fast_4G | 96 | 0.02 | -0.14-0.56 | 0.14 | not distinguishable from zero |
| No_Throttle | 95 | 0.06 | -0.15-0.66 | 0.19 | not distinguishable from zero |

So the data supports an image saving only on bandwidth-bound links. On fast links the effect may be small, or the sample
(median image share only 13% of the bytes) may be too small to see it; the intervals are wide. The API uses the point
estimates and labels the fast-network savings `basis: "measured-weak"` (Slow 3G is `"measured"`).

**Selection bias on Slow 3G:** 40% of baseline runs but only 26% of `no_images` runs failed (90 s timeouts), and the sites
that dropped out are much heavier (median 5.7 MB vs 1.2 MB). The Slow 3G slope therefore describes pages that load within
90 s; for heavy pages the true effect is probably larger but cannot be measured this way.

## 9. Known issues and suggested next work

1. **Savings shown per method are not tree output.** *Lazy-load images* uses the measured effect (section 8: solid on Slow 3G, `measured-weak` on fast links); the other three methods still use `removed bytes / assumed throughput * 0.6` in `api/main.py` (`ACTIONS`, `THROUGHPUT_BPS`, `basis: "estimate"`). Replace them with measured effects once those variants exist for many sites, collected with `--paired` and alternating order. The byte-throughput formula was never validated against measurements (the earlier "far too low" comparison used drift-inflated data), so treat those three as unproven.
2. **Run the other variants and repeats 2-3** across all URLs (`run_tomorrow.bat`), then re-run `train_new.py` / `train_model.py` and refresh the report and `reports/fig_*.png` (the figures and `reports/results.json` still describe the old dataset; `train_all.py` reads the old `new_preprocessed.csv` and needs the same adapter `train_new.py` has).
3. **Unseen URLs get a median page profile** in the API unless the caller sends `page` bytes. The extension now does (`pageWeight()` in `extension/content.js`, Resource Timing), but Chrome hides the size of cross-origin responses without `Timing-Allow-Origin`, so only 44-93% of the true bytes were disclosed on the 3 pages tried. The API prices hidden requests at the median size of their type; the result was within 3% of the CDP total on 2 pages and 65% too high on theguardian.com (many tiny trackers). Untested inside Chrome itself, only the same code run through Playwright.
4. **Network profile is guessed from RTT** (`network_profile()` in `api/main.py`: <30 ms = No_Throttle, <200 ms = Fast_4G, else Slow_3G). That is crude.
5. **Extension is untested in a real browser** and its optimizations are partial (section 4). The dashboard's Optimizations page toggles do not control the extension (popup toggles do).
6. **Data limits**: one machine, simulated networks, one repeat per combination, failed loads dropped (mostly on Slow 3G, so the slowest pages are under-represented). The unexplained Fast 4G error is the best modelling lead.
7. `predict` for wikipedia.org returned 1.25 s, far below the model's typical error scale; sanity-check profiles with very few bytes (the API warns for pages under 250 KB).

## 10. Repo hygiene

Git-ignored: `__pycache__/`, `.venv/`, logs (`collected/*_log*.txt`), `collected/live_runs.jsonl`, `collected/os_*.csv`, `agent_state.json`, `network_requests.csv`.
`collected/runs.csv` is the only copy of the new dataset: commit it, and merge by appending if several people collect (the collector keys rows on url + network + cpu + cache + variant + repeat, so give each machine a different `--out` file and concatenate).
