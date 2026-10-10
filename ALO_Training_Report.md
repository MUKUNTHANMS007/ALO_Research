# ALO Training Report (v2)

Adaptive Latency Optimization: predicting page load time from page size and network conditions.
Generated 2026-10-08 from `collected/runs.csv` by `train_new.py` (evaluation) and `train_model.py` (export).
The earlier 71-site report is kept in `reports/ALO_Training_Report_v1_71sites.md`. Its figures in `reports/results.json` and `reports/fig_*.png` still describe that old dataset.

## 1. Summary

- **Task:** predict `page_load_time_ms` for a website under a network condition.
- **Data:** 715 usable runs from **277 websites** (baseline, cold cache, 1x CPU), up from 548 runs / 71 sites.
- **Exported model:** decision tree on total page bytes + network profile. On websites it never saw: **R² 0.72 ± 0.05, mean error 5.2 s (median 1.95 s)**, against 12.1 s for guessing the median.
- **Best accuracy:** Extra Trees with 11 composition features reaches R² 0.76 (4.7 s). Adding TTFB gives R² 0.80 (4.5 s), but TTFB is measured during the load, so it cannot be an input to a predict-before-loading system.
- **The old model does not transfer:** trained on the first dataset, it scores R² 0.48 / 7.6 s on the new data. The new pages are much faster and more varied (257 runs under 3 s; the old data had none). The exported model is therefore retrained on the new data. The old one is backed up in `model/v1_old/`.
- **Weak spots:** Fast 4G (mean error 7.3 s) and unthrottled loads (R² 0.44).

## 2. Data

| | |
|---|---|
| Collector runs in file | 1,763 rows (313 URLs), 135 further failures in `collected/failed.csv` |
| Used for training | baseline variant, cold cache, cpu_1x, success, more than 1 request, over 100 ms: **715 runs, 277 sites** |
| Per network | No_Throttle 265, Fast_4G 257, Slow_3G 193 |
| Speed classes | under 3 s: 257, 3 to 8 s: 190, 8 s or more: 268 |

Failures were again concentrated on Slow 3G, so the slowest pages are under-represented there. Only repeat 1 was collected (no repeats, no warm-cache or 4x CPU sweep beyond a small sample).

## 3. Evaluation protocol

Target `log1p(load time)`. Folds are grouped by website (5 folds, repeated 3 times, 15 test folds); a site never appears in both training and test. Tree depth and leaf size are tuned inside each training fold.

## 4. Results (unseen websites)

| Model | R² | Mean error |
|---|---|---|
| Median guess | n/a | 12.06 s |
| **Decision tree, total bytes + network (exported)** | **0.717 ± 0.051** | 5.16 s |
| Decision tree, 11 features | 0.701 | 5.21 s |
| Extra Trees, total bytes + network | 0.680 | 5.83 s |
| Extra Trees, 11 features | 0.763 | 4.71 s |
| Extra Trees, 11 features + TTFB (not usable pre-load) | 0.797 | 4.53 s |
| Tree, total bytes + TTFB (not usable pre-load) | 0.741 | 5.08 s |

As before, extra features do not help the single tree, so the exported model keeps the two-input design.

Error by network (tree, out of fold):

| Network | Runs | R² within group | Mean error |
|---|---|---|---|
| Slow_3G | 193 | 0.894 | 4.40 s |
| Fast_4G | 257 | 0.608 | 7.34 s |
| No_Throttle | 265 | 0.440 | 3.34 s |

Unthrottled R² improved from -0.37 (old data) to 0.44, probably thanks to the wider spread of pages. Why Fast 4G has the largest error has not been investigated.

## 5. Exported model

`model/alo_tree.joblib`: depth 5, 26 leaves, minimum leaf 10. Splitting power: total bytes 59%, Slow 3G 34%, No_Throttle 5%, Fast 4G 3%. `model/url_profiles.json` holds page profiles for 277 URLs (old-dataset profiles are kept for URLs the new collector did not cover). Restart the API (`python -m uvicorn api.main:app --port 8000`) to load it.

## 6. Method-removal experiment (no_images)

Paired against the baseline for the same URL and network (cold cache, 1x CPU):

| Network | Pairs | Median change in load time |
|---|---|---|
| All | 795 | -668 ms (LCP -412 ms, bytes -90 KB) |
| Fast_4G | 284 | -1,239 ms |
| Slow_3G | 219 | -587 ms |
| No_Throttle | 292 | -396 ms |

Blocking images removes them entirely, so these are upper bounds on what image optimisation could save. The other variants (third-party scripts, fonts, media) were only run on 3 sites and are not enough to conclude anything.

## 7. Limits

1. One machine, simulated networks, one repeat per combination.
2. Survivorship: failed loads are dropped, mostly on Slow 3G.
3. Unseen URLs use a median page profile in the API.
4. Per-method savings in the dashboard are still the byte-throughput estimate, not model output; only `no_images` has measured data at scale.
5. Some metrics (LCP, TTFB, CPU) are collected but not model inputs.

## 8. Next steps

1. Run the other variants (third-party scripts, fonts, media) and repeats 2 and 3 across all URLs.
2. Investigate Fast 4G error, and whether a predicted TTFB proxy (e.g. server region) can be added.
3. Use `no_images` and later variant measurements to replace the estimated savings in the dashboard.

## 9. Reproduce

```
python collect.py --quick --repeats 1   # resumable collection
python train_new.py                     # evaluation on collected/runs.csv
python train_model.py                   # export model/alo_tree.joblib and model/url_profiles.json
python -m uvicorn api.main:app --port 8000
```
