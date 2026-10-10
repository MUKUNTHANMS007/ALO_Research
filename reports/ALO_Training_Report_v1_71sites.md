# ALO Training Report

Adaptive Latency Optimization: predicting page load time from page size and network conditions.
Generated 2026-10-05 by `train_all.py` (raw numbers in `reports/results.json`, figures in `reports/`).

## 1. Summary

- **Task:** predict `page_load_time_ms` for a website under a given network condition, so ALO can estimate the effect of load-reduction methods.
- **Headline result:** the exported **decision tree reaches R² 0.72 (±0.06) with a mean error of 2.65 s on websites it never saw**, against 6.66 s for guessing the median (60% lower error). It uses only two inputs: total page bytes and network profile.
- **Best accuracy overall:** Extra Trees reaches R² 0.80 and 2.47 s mean error (11 features), but it cannot be turned into readable rules, and with total bytes + network alone it is only about 0.03 R² ahead of the tree.
- **Simple beats complex here:** with about 70 websites, adding page features made the tree *worse* (R² 0.72 with 1 size feature, 0.65 with 11, 0.62 with 48). The exported model therefore uses the simple input set.
- **Leakage matters:** adding outcome columns measured during the load (request durations, task time) inflates R² by 0.12 to 0.17. They are excluded.
- **Weak spot:** on unthrottled (fast) networks the model has no skill (R² −0.37 within that group). Load time there depends on things not in the data (server distance, scripts, device).
- **Collector data is not yet usable for training:** `collected/runs.csv` has 3 websites and 46% failed runs (see section 8).

## 2. Datasets

| File | Rows | What it is | Used for |
|---|---|---|---|
| `page_runs.csv` | 801 | One row per page load: 89 URLs × 3 network profiles × 3 repeats; load time, bytes by type, CPU/memory | Source of `new_preprocessed.csv` |
| `network_requests.csv` | 80,877 | One row per network request, linked by `run_id` | Aggregated into per-run features |
| `new_preprocessed.csv` | 801 × 96 | The two files merged and engineered (counts by type/method/MIME, durations, rates, logs) | **Training data** |
| `collected/runs.csv` | 170 × 43 | New collector output (LCP/FCP/TTFB, CPU, variants) | Reported separately (section 8) |

All three network profiles are simulated throttles (No_Throttle, Fast_4G, Slow_3G), collected on one day (2026-09-26) from one machine.

## 3. Cleaning

| Step | Rows | Domains |
|---|---|---|
| Raw runs | 801 | 89 |
| Keep `navigation_status = success` | 607 | |
| Drop broken captures (1 request or fewer, 0 bytes, or load under 100 ms) | **548** | **71** |

- 194 of 801 runs (24%) failed navigation and were removed. Failures were more common on Slow 3G, so the kept data has fewer runs there (Slow 3G 134, Fast 4G 207, No_Throttle 207). The hardest pages on slow networks are therefore under-represented, which makes the Slow 3G numbers somewhat optimistic.
- Constant columns (no information): both Chrome CPU columns (all zero), day/month/weekend, `http_3xx_count`, and both disk-cache columns. They are not used.

Load time in the kept data (seconds):

| Network | p10 | Median | p90 | Spearman: bytes vs load time |
|---|---|---|---|---|
| No_Throttle | 3.4 | 4.9 | 13.1 | 0.42 |
| Fast_4G | 3.8 | 6.9 | 18.4 | 0.91 |
| Slow_3G | 5.2 | 11.5 | 45.2 | 0.98 |

Page size explains load time almost perfectly on slow networks and only moderately on an unthrottled one. This shapes every result below.

## 4. Evaluation protocol

- **Target:** `log1p(page_load_time_ms)` (load times are heavily skewed). Errors are reported back in milliseconds.
- **Split:** grouped by website domain, so no site appears in both training and test data. A random split would leak, because each site appears 6 to 9 times.
- **Repetition:** 5 folds, repeated 3 times with different random fold assignments (15 test folds). Reported values are means; ± is the standard deviation across folds.
- **Tuning:** the decision tree's depth and leaf size are tuned *inside each training fold*, never on the test fold.
- **Inputs (unless stated):** 11 page-composition features + network profile. No load-time outcomes are used as inputs.

## 5. Experiments

### 5.1 Model comparison (11 features + network profile)

![Model comparison](reports/fig_model_comparison.png)

| Model | R² (log) | Mean error (ms) | Median error (ms) | Median % error |
|---|---|---|---|---|
| Median baseline | −0.10 ± 0.15 | 6,660 ± 1,047 | 2,910 | 52.5% |
| Linear regression | 0.50 ± 0.09 | 5,189 ± 949 | 2,066 | 30.3% |
| Ridge (alpha 10) | 0.52 ± 0.09 | 5,038 ± 867 | 2,024 | 30.2% |
| **Decision tree (tuned)** | **0.65 ± 0.10** | **3,031 ± 619** | **1,551** | **20.4%** |
| Hist gradient boosting | 0.77 ± 0.09 | 2,680 ± 520 | 1,371 | 16.8% |
| Random forest | 0.79 ± 0.06 | 2,485 ± 427 | 1,207 | 16.6% |
| Extra trees | 0.80 ± 0.06 | 2,472 ± 506 | 1,193 | 16.5% |

- Tree-based models beat linear ones by a wide margin: the relationship between size and time is not a straight line (it depends on the network).
- Ensembles beat the single tree by about 0.12 to 0.14 R² on this feature set. The fold standard deviation is 0.06 to 0.10, so differences between the three ensembles are noise.
- The single tree still cuts the median-guess error by more than half.

### 5.2 Feature-set ablation, and the cost of leakage

![Ablation](reports/fig_ablation.png)

| Inputs (always + network profile) | Features | Tree R² | Tree error (ms) | Extra trees R² | Extra trees error (ms) |
|---|---|---|---|---|---|
| Network profile only | 3 | 0.21 | 6,237 | 0.21 | 6,237 |
| **Total bytes** | 4 | **0.72** | **2,646** | 0.75 | 2,624 |
| Compact (total bytes, script bytes, request count, DOM nodes) | 7 | 0.71 | 2,840 | 0.77 | 2,662 |
| Small (11 composition features) | 14 | 0.65 | 3,031 | 0.80 | 2,473 |
| All composition features | 51 | 0.62 | 3,224 | 0.79 | 2,660 |
| Small + outcome columns (**leaky**) | 19 | 0.82 | 3,044 | 0.92 | 1,852 |

Findings:
1. **Network profile alone explains little (R² 0.21). Adding total page bytes lifts it to 0.72.** Total size is the one feature that matters.
2. **More features hurt the single tree.** It overfits with only 71 websites. This is why the exported model uses total bytes only.
3. **Leakage is large.** Adding request duration, task and script duration, and failed-request counts raises R² by 0.17 (tree) and 0.12 (Extra trees). Those columns are measured during the load, so a real "predict before loading" system cannot use them. They are excluded from every other result.

### 5.3 Error by network condition (tree, out of fold)

![Predicted vs actual](reports/fig_pred_vs_actual.png)

| Network | Runs | Mean actual (s) | Mean error (s) | Median % error | R² within group |
|---|---|---|---|---|---|
| Slow_3G | 134 | 20.7 | 3.0 | 10.9% | 0.93 |
| Fast_4G | 207 | 9.3 | 2.1 | 16.7% | 0.74 |
| No_Throttle | 207 | 6.7 | 3.8 | 31.4% | **−0.37** |

The overall R² is high largely because the model separates the three networks. **Inside the unthrottled group it does worse than predicting that group's average**, and the scatter plot shows horizontal bands there: the model predicts a few fixed values for pages whose real load times vary from 3 to 25 s. Page size is not what makes an unthrottled load slow (Spearman 0.42).

### 5.4 Tree depth

| Max depth | R² | Mean error (ms) |
|---|---|---|
| 2 | 0.58 | 4,143 |
| 3 | 0.63 | 3,607 |
| 4 | 0.66 | 3,154 |
| **5** | **0.68** | **3,023** |
| 6 | 0.68 | 3,021 |
| 8 | 0.65 | 3,084 |
| 10 / unlimited | 0.66 | 3,042 |

Accuracy rises until depth 5 to 6 and then plateaus. Deeper trees add nothing, so a depth of 5 or 6 is enough (leaf size fixed at 10 for this test).

### 5.5 Log versus raw target

| Target | R² (log) | Mean error (ms) |
|---|---|---|
| log1p(load time) | 0.65 | 3,031 |
| Raw milliseconds | 0.68 | 2,989 |

The difference is far inside the fold noise (±0.10), so the choice of target does not matter for the tree. The log target was kept for consistency.

### 5.6 Speed classes (fast, medium, slow)

A classification view of the same problem, using the same inputs:

| Model | Accuracy | Macro F1 |
|---|---|---|
| Majority baseline | 0.59 | 0.37 |
| Decision tree (balanced) | 0.84 ± 0.06 | 0.83 ± 0.06 |
| Extra trees (balanced) | 0.91 ± 0.03 | 0.90 ± 0.04 |

Classes: medium (3 to 8 s) 324 runs, slow (8 s or more) 224 runs, **fast (under 3 s) 0 runs**. No page in this dataset loaded in under 3 s, even unthrottled, so this is in effect a two-class problem (medium versus slow). The tree misclassifies 29 of 324 medium and 52 of 224 slow runs in the first repeat. A "fast" class needs data from lighter pages and a faster test machine.

### 5.7 Learning curve

![Learning curve](reports/fig_learning_curve.png)

| Share of training websites | Tree R² | Extra trees R² |
|---|---|---|
| 20% | 0.16 | 0.62 |
| 40% | 0.47 | 0.73 |
| 60% | 0.63 | 0.76 |
| 80% | 0.64 | 0.77 |
| 100% | 0.68 | 0.80 |

Both curves are still rising at 100%. **More websites would help**, the single tree more than the ensemble. This is the strongest argument for the planned 300-URL collection.

### 5.8 Where the model fails

Largest errors by website (tree, out of fold):

| Website | Mean actual (s) | Mean error (s) | Direction |
|---|---|---|---|
| baidu.com | 20.2 | 12.4 | predicted too fast |
| mail.ru | 23.7 | 11.7 | predicted too fast |
| ok.ru | 23.8 | 11.5 | predicted too fast |
| rutube.ru | 21.5 | 10.3 | predicted too fast |
| rakuten.co.jp | 27.8 | 10.2 | predicted too fast |
| yahoo.com | 18.4 | 9.1 | predicted too slow |
| naver.com | 21.9 | 7.8 | predicted too slow |

Best predicted: namu.wiki (0.15 s error), espn.com (0.26 s), ebay.com (0.34 s), chatgpt.com (0.35 s), amazon.co.jp (0.37 s).

The worst cases are mostly Russian and East Asian sites that are much slower than their size suggests. A plausible cause is **distance to the server from the test machine**, which is not in the data. This is a hypothesis, not something tested here; it could be checked by adding time-to-first-byte (the new collector records it).

## 6. Final models

| | Exported decision tree (used by the API) | Extra trees (saved, not wired in) |
|---|---|---|
| File | `model/alo_tree.joblib` | `model/alo_extratrees.joblib` |
| Inputs | total network bytes + network profile | 11 composition features + network profile |
| Settings | depth 6, minimum leaf 20, 21 leaves | 300 trees, minimum leaf 4 |
| R² on unseen sites | **0.72 ± 0.06** | 0.80 ± 0.06 |
| Mean error | **2.65 s** (median 1.33 s) | 2.47 s |
| Trained on | 548 runs, 71 websites | 548 runs, 71 websites |

The tree's splitting power: total network bytes 60%, Slow 3G 28%, No_Throttle 12%. The per-resource savings shown in the dashboard are **not** model output: each is estimated as removed bytes ÷ network throughput × 0.6, because a shallow tree cannot price small byte cuts.

Extra trees remains available as an optional "high accuracy mode". Its gain over the tree is small once the inputs are the same (0.75 versus 0.72), so the tree stays the default for explainability.

## 7. Note on earlier numbers

Earlier in the project, accuracy of R² 0.86 (and later 0.70) was quoted. Those were optimistic or used a single fold assignment: parameters were tuned on the same folds used for scoring, the first cleaning removed the hardest pages, and one fold assignment gives a noisy estimate (±0.06 to ±0.10). **The figures in this report replace them.** The `/api/health` endpoint and the dashboard's Model page report the repeated-CV figures above.

## 8. New collector data (`collected/runs.csv`)

| | |
|---|---|
| Rows | 170 (91 succeeded, 79 failed) |
| Websites | 3 (google.com, facebook.com, youtube.com) |
| Failure cause | 78 `ERR_INTERNET_DISCONNECTED`, 1 timeout: the connection dropped during collection |
| CPU capture | works: 0% of rows are zero (mean peak 140%, i.e. more than one core) |
| New targets | TTFB, FCP, LCP, CLS and TBT are all recorded |

**Not enough to train on** (3 websites). What it does show:
- CPU slowdown behaves as expected: median load 2.8 s at 1× versus 3.8 s at 4×.
- The blocking variants produce measurable differences. Median change in load time against the matching baseline (18 pairs each, 3 sites only): third-party scripts removed −904 ms, media removed −251 ms, images removed −143 ms, fonts removed −38 ms. These are an upper bound on what each method could save, and they come from three sites, so they should not be generalised.

## 9. Limits

1. **71 websites on one day from one machine.** The learning curve is still rising, so the estimates will move as data grows.
2. **Throttled, not real, networks.** Real mobile conditions vary more.
3. **Survivorship in the data.** Failed loads were dropped, and failures were more common on slow networks.
4. **No skill on unthrottled networks** (R² −0.37 inside that group).
5. **Unseen URLs use a median page.** The model is given page bytes from the training data; for a new URL the API assumes the median site.
6. **Savings per method are estimates.** They are not model output and have not been measured end to end.
7. **Device load is not a model input.** The dataset's CPU columns were all zero.

## 10. Next steps

1. **Fix collector reliability** (connection drops) and run it on 300 or more URLs. The learning curve says this is the highest-value step.
2. **Add TTFB and LCP as targets and inputs.** LCP is closer to what users feel, and TTFB may explain the server-distance failures in section 5.8.
3. **Collect real device load** (CPU, memory) with each run so it can become a model input.
4. **Run the A/B method variants on all URLs, including combinations**, so per-method savings can be measured instead of estimated.
5. **Re-run `train_all.py`** on the new data and replace this report.

## 11. Reproduce

```
python train_all.py        # all experiments -> reports/results.json and reports/fig_*.png
python train_model.py      # export the API tree -> model/alo_tree.joblib, model/url_profiles.json
python -m uvicorn api.main:app --port 8000   # serve the API and dashboard
```
