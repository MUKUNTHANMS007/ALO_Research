# ALO Training Report (v2)

Adaptive Latency Optimization: predicting page load time from page size and network conditions.
Generated 2026-10-08 from `collected/runs.csv` by `train_new.py` (evaluation) and `train_model.py` (export).
The earlier 71-site report is kept in `reports/ALO_Training_Report_v1_71sites.md`. Its figures in `reports/results.json` and `reports/fig_*.png` still describe that old dataset.

## 1. Summary

- **Task:** predict `page_load_time_ms` for a website under a network condition.
- **Data:** 967 usable runs from **279 websites** (baseline, cold cache, 1x CPU; 715 repeat-1 + 252 repeat-2 runs), up from 548 runs / 71 sites. **Update 2026-10-17:** the models and the numbers in this bullet and in the paragraph below were refreshed on this larger set (Extra Trees R² 0.745, mean error 4.4 s, median 1.6 s; single tree 0.698 / 5.0 s; Extra Trees won 13 of 15 paired folds, p = 0.0003). Tables further down that were not repeated still describe the 715-run data from 2026-10-08.
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

As before, extra features do not help the single tree. They do help an ensemble, and the exported predictor was switched accordingly (2026-10-14): on the same 15 folds, paired against the tree, Extra Trees with 11 features and `min_samples_leaf=2` scored **R² 0.769 ± 0.032, mean error 4.34 s, median error 1.58 s**, and won in 15 of 15 folds (mean ΔR² +0.052, Wilcoxon p = 0.0001). `min_samples_leaf` was picked from {2, 4, 8} on this same CV, so the figure is slightly optimistic. Random forest (0.764) and gradient boosting (0.760) landed in the same range. The single tree is still exported for the readable rule view. Extra Trees improves every network (R² / mean error, same folds): Slow 3G 0.90 / 4.4 s → 0.92 / 3.4 s, Fast 4G 0.58 / 7.6 s → 0.65 / 6.5 s, No_Throttle 0.44 / 3.3 s → 0.55 / 2.9 s. Fast 4G is still the weakest, and its slowest 10% of loads still average a 33 s error. The per-network table below is for the single tree. **Update 2026-10-21 (4-feature predictor, 967 runs):** R² 0.748 ± 0.026, mean error 4.46 s, median 1.65 s, beating the tree in 15 of 15 paired folds (ΔR² +0.050, p = 0.0001); per network (R² / mean error) Slow 3G 0.92 / 3.3 s, Fast 4G 0.64 / 6.1 s, No_Throttle 0.53 / 3.6 s. **Prediction range:** the ensemble's tree-vote interval was overconfident (nominal 80% covered 67% of held-out pages), so the API now returns a range calibrated on held-out errors per network and predicted-speed bucket; cross-fitted coverage is 77.8% overall (Fast 4G 78%, No_Throttle 79%, Slow 3G 75%). **Importance (permutation, held-out sites):** network 0.98, total bytes 0.23, script bytes 0.14, request count 0.11, image bytes 0.05.

Error by network (tree, out of fold):

| Network | Runs | R² within group | Mean error |
|---|---|---|---|
| Slow_3G | 193 | 0.894 | 4.40 s |
| Fast_4G | 257 | 0.608 | 7.34 s |
| No_Throttle | 265 | 0.440 | 3.34 s |

Unthrottled R² improved from -0.37 (old data) to 0.44, probably thanks to the wider spread of pages. Why Fast 4G has the largest error has not been investigated.

## 5. Exported model

`model/alo_et.joblib` is the predictor (Extra Trees, 300 trees, 4 page features - total bytes, script bytes, request count, image bytes - + network; 2026-10-21: the other 7 of the 11 composition features were dropped because they changed nothing on unseen websites, paired dR2 +0.003, p = 0.93, while removing script or image bytes did hurt). `model/alo_tree.joblib` is the single tree used for the rule view. Tree details: depth 5, 26 leaves, minimum leaf 10. Splitting power: total bytes 59%, Slow 3G 34%, No_Throttle 5%, Fast 4G 3%. `model/url_profiles.json` holds page profiles for 277 URLs (old-dataset profiles are kept for URLs the new collector did not cover). Restart the API (`python -m uvicorn api.main:app --port 8000`) to load it.

## 6. Method-removal experiment (no_images)

Paired against the baseline for the same URL and network (cold cache, 1x CPU):

| Network | Pairs | Median change in load time |
|---|---|---|
| All | 795 | -668 ms (LCP -412 ms, bytes -90 KB) |
| Fast_4G | 284 | -1,239 ms |
| Slow_3G | 219 | -587 ms |
| No_Throttle | 292 | -396 ms |

Blocking images removes them entirely, so these are upper bounds on what image optimisation could save. **They are also inflated by drift:** the `no_images` run of a URL was taken a median of 71 h after its baseline, and pages with zero image bytes still came out faster (Fast 4G 39%, No_Throttle 8%, Slow 3G 2%). Back-to-back re-measurement (2026-10-17, `collect.py --paired`, 253 pairs less than 1 h apart, alternating order) removed the drift: median "saving" fell to 3% (No_Throttle), 10% (Fast 4G) and 3% (Slow 3G). It also exposed an order effect: on the fast networks the run that goes second is 14-19% faster whatever the variant (warm DNS/CDN/server caches); Slow 3G has none. With that modelled (`saving = a + slope x image share + c x order`), the image slope is **0.94 on Slow 3G (90% CI 0.88-0.97, significant)** but **0.02 on Fast 4G (-0.14 to 0.56) and 0.06 on No_Throttle (-0.15 to 0.66), not distinguishable from zero**. Caveat for Slow 3G: 40% of baseline runs but only 26% of `no_images` runs timed out, and the sites lost are much heavier (median 5.7 MB vs 1.2 MB), so that slope describes pages that load within 90 s.

## 7. Limits

1. One machine, simulated networks, one repeat per combination.
2. Survivorship: failed loads are dropped, mostly on Slow 3G.
3. Unseen URLs use a median page profile in the API.
4. Per-method savings in the dashboard are not model output. Images use the measured slopes above (solid on Slow 3G only); the other three methods are still unvalidated byte-throughput estimates.
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
