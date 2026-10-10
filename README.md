# ALO - Adaptive Latency Optimization

Predicts how long a web page takes to load from its size and the network it is loaded on, estimates what load-reduction
methods (lazy loading, script deferral, ...) would save, and applies some of them live in Chrome through an extension.
A dashboard shows predictions next to the load times measured in the browser.

> Status (2026-10-10): working prototype. Model trained on 715 runs / 277 websites. Hand-over notes are in section 9.

## 1. What is in this repo

| Part | Files | What it does |
|---|---|---|
| Data collector | `collect.py`, `urls.txt` | Loads URLs in headless Chromium (Playwright) under simulated networks / CPU / cache / blocking variants and records timings, bytes, CPU. |
| Training / evaluation | `train_new.py`, `train_model.py` | `train_new.py` evaluates models on `collected/runs.csv`; `train_model.py` exports the model the API uses. |
| Legacy training (first dataset) | `clean.py`, `train_tree.py`, `train_v2.py`, `train_all.py`, `new_preprocessed.csv`, `page_runs*.csv` | Original 71-site experiments. `train_all.py` produced `reports/results.json` and the `reports/fig_*.png` figures (old data only). |
| API + dashboard | `api/main.py`, `ui/` | FastAPI serves predictions at `/api/*` and the dashboard at `/`. |
| Browser extension | `extension/` | Chrome MV3 extension: applies optimizations to pages you open and reports measured load time to the API. |
| Reports | `ALO_Training_Report.md` (current), `reports/ALO_Training_Report_v1_71sites.md` (old) | Results and limits. Read `ALO_Training_Report.md` first. |
| Saved models | `model/alo_tree.joblib` (used by API), `model/url_profiles.json`, `model/alo_extratrees.joblib` (old, not wired in), `model/v1_old/` (first model backup) | |
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
the Model page shows 715 runs / 277 sites.

API endpoints (`api/main.py`):

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | model metrics |
| `GET /api/system` | live CPU/RAM/RTT of this machine and the network profile guessed from RTT |
| `POST /api/predict` `{url, profile?, disabled?, min_saving_ms?}` | baseline prediction + per-method savings |
| `POST /api/report` `{url, load_ms, stats, disabled}` | called by the extension after a page load; stored with predicted vs measured |
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

## 5. Data

| File | Rows | Notes |
|---|---|---|
| `collected/runs.csv` | ~1,760 | **Current training data.** Output of `collect.py`. Training uses only `variant=baseline`, `cpu_1x`, `cold` cache, success, >1 request, >100 ms: 715 runs, 277 sites. Other rows are the `no_images` variant (~800) and small samples of other variants/CPU/cache. |
| `collected/failed.csv` | ~135 | Failed runs (timeouts, resets); not used for training. |
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
- Exported model: decision tree on `total_network_bytes` + network profile only (more features made the single tree worse with this little data).
- Do not use outcome columns measured during the load (request durations, TTFB, task time) as inputs: that is leakage and inflated R2 by 0.12 to 0.17 in the first study. TTFB gives R2 0.80 in `train_new.py` but is unusable for "predict before loading".
- `train_model.py` overwrites `model/alo_tree.joblib`. Copy the old one first (as `run_tomorrow.bat` does into `model/v2_backup/`).

## 7. Current results (grouped CV, unseen websites)

| Model | R2 (log) | Mean error |
|---|---|---|
| Median guess | n/a | 12.1 s |
| **Decision tree, bytes + network (exported)** | **0.72 +/- 0.05** | 5.2 s (median 1.9 s) |
| Extra Trees, 11 features | 0.76 | 4.7 s |
| Extra Trees + TTFB (not usable pre-load) | 0.80 | 4.5 s |

Per network (tree): Slow 3G R2 0.89, Fast 4G 0.61 (largest error, 7.3 s, cause not investigated), No_Throttle 0.44.
The model trained on the first dataset transfers badly to the new data (R2 0.48), so do not mix the datasets blindly.
Full details: `ALO_Training_Report.md`.

## 8. Measured effect of blocking images (`no_images` variant)

795 paired runs (same URL + network, baseline vs images blocked): median load time change -668 ms (Fast 4G -1,239 ms,
Slow 3G -587 ms, No_Throttle -396 ms). This is an upper bound (images are removed, not compressed). The other variants
(third-party scripts, fonts, media) have only 3 sites of data so far.

## 9. Known issues and suggested next work

1. **Savings shown per method are an estimate, not model output**: `removed bytes / assumed throughput * 0.6` in `api/main.py` (`ACTIONS`, `THROUGHPUT_BPS`). Replace with measured effects from the collector variants once they exist for many sites.
2. **Run the other variants and repeats 2-3** across all URLs (`run_tomorrow.bat`), then re-run `train_new.py` / `train_model.py` and refresh the report and `reports/fig_*.png` (the figures and `reports/results.json` still describe the old dataset; `train_all.py` reads the old `new_preprocessed.csv` and needs the same adapter `train_new.py` has).
3. **Unseen URLs get a median page profile** in the API; the model only knows page bytes for the 277 stored sites. For real use, the extension could send the page's actual byte count (e.g. from `performance.getEntriesByType('resource')`) to `/api/predict`.
4. **Network profile is guessed from RTT** (`network_profile()` in `api/main.py`: <30 ms = No_Throttle, <200 ms = Fast_4G, else Slow_3G). That is crude.
5. **Extension is untested in a real browser** and its optimizations are partial (section 4). The dashboard's Optimizations page toggles do not control the extension (popup toggles do).
6. **Data limits**: one machine, simulated networks, one repeat per combination, failed loads dropped (mostly on Slow 3G, so the slowest pages are under-represented). The unexplained Fast 4G error is the best modelling lead.
7. `predict` for wikipedia.org returned 1.25 s, far below the model's typical error scale; sanity-check profiles with very few bytes (the API warns for pages under 250 KB).

## 10. Repo hygiene

Git-ignored: `__pycache__/`, `.venv/`, logs (`collected/*_log*.txt`), `collected/live_runs.jsonl`, `network_requests.csv`.
`collected/runs.csv` is the only copy of the new dataset: commit it, and merge by appending if several people collect (the collector keys rows on url + network + cpu + cache + variant + repeat, so give each machine a different `--out` file and concatenate).
