"""Calibrate the dashboard's per-method savings from measured data. Output: model/savings_calibration.json

Image effect: for each URL + network the collector measured a baseline run and a `no_images` run (images blocked).
saving fraction = 1 - load(no_images) / load(baseline). The two runs were collected a median of ~71 h apart, so part of
that fraction is drift, not images: pages with no image bytes at all still came out 39% faster on Fast 4G. The fit
therefore keeps an intercept and uses only the slope on the page's image byte share:

    fraction saved by removing every image  =  slope[network] * image_bytes / total_bytes

(median regression per network). This is an upper bound for lazy loading, which removes images only from the initial
load. The API scales it by the fraction of images a method cuts.

Also stores the median bytes per request by resource type (old dataset), used to fill in cross-origin resources whose
size the browser hides from the extension."""
import json
import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import QuantileRegressor

warnings.filterwarnings('ignore')
NETS = ['No_Throttle', 'Fast_4G', 'Slow_3G']
TYPES = {'document': ['document'], 'script': ['script'], 'stylesheet': ['stylesheet'], 'image': ['image'],
         'font': ['font'], 'xhr_fetch': ['xhr', 'fetch'], 'media': ['media'], 'other': ['other']}


def paired_images(path='collected/runs.csv'):
    r = pd.read_csv(path)
    r = r[(r.cpu_profile == 'cpu_1x') & (r.cache_state == 'cold') & (r.navigation_status == 'success')
          & r.variant.isin(['baseline', 'no_images']) & (r.page_load_time_ms > 100) & (r.request_count > 1)]
    key = ['url', 'network_profile', 'repeat_number']
    cols = ['page_load_time_ms', 'image_bytes', 'total_network_bytes']
    b = r[r.variant == 'baseline'].drop_duplicates(key).set_index(key)
    n = r[r.variant == 'no_images'].drop_duplicates(key).set_index(key)
    j = b[cols + ['timestamp_utc']].join(n[['page_load_time_ms', 'timestamp_utc']], rsuffix='_noimg', how='inner').reset_index()
    j['share'] = j.image_bytes / j.total_network_bytes
    j['frac'] = 1 - j.page_load_time_ms_noimg / j.page_load_time_ms
    j['gap_h'] = (pd.to_datetime(j.timestamp_utc_noimg) - pd.to_datetime(j.timestamp_utc)).dt.total_seconds().abs() / 3600
    return j


MIN_ADJACENT_SITES = 100  # back-to-back pairs (collect.py --paired) are used on their own once there are this many sites


def fit(x):
    q = QuantileRegressor(quantile=0.5, alpha=0, solver='highs').fit(x[['share']], x.frac)
    return float(q.intercept_), float(q.coef_[0])


def main():
    j = paired_images()
    adj = j[j.gap_h < 1]
    if adj.url.nunique() >= MIN_ADJACENT_SITES:
        j, pairing = adj, 'adjacent (<1 h apart)'
    else:
        pairing = f'drifted: median gap {j.gap_h.median():.0f} h, only {adj.url.nunique()} sites with back-to-back pairs'
    print('pairing:', pairing)
    rng = np.random.default_rng(0)
    sites = j.url.unique()
    out = {'images': {}, 'pairing': pairing}
    for net in NETS:
        x = j[j.network_profile == net]
        a, b = fit(x)
        boots = []
        for _ in range(100):  # resample websites, not rows: runs of one site are not independent
            pick = rng.choice(sites, len(sites))
            xs = pd.concat([x[x.url == s] for s in pick if (x.url == s).any()])
            boots.append(fit(xs)[1])
        lo, hi = np.percentile(boots, [5, 95])
        out['images'][net] = {'slope': round(b, 3), 'slope_ci90': [round(float(lo), 3), round(float(hi), 3)],
                              'drift_offset': round(a, 3), 'pairs': int(len(x)),
                              'zero_image_pairs': int((x.image_bytes < 1000).sum())}
        print(f'{net:12s} pairs {len(x):4d}  slope {b:.3f} (90% CI {lo:.3f}..{hi:.3f})  drift offset {a:.3f}')

    old = pd.read_csv('new_preprocessed.csv')
    old = old[(old.navigation_status == 'success') & (old.request_count > 1)]
    rb = {}
    for k, cols in TYPES.items():
        cnt = sum(old[f'request_count_type_{c}'] for c in cols)
        byt = sum(old[f'request_bytes_type_{c}'] for c in cols)
        m = cnt > 0
        rb[k] = int(round((byt[m] / cnt[m]).median()))
    out['request_bytes'] = rb
    print('median bytes per request:', rb)
    json.dump(out, open('model/savings_calibration.json', 'w'), indent=1)
    print('saved model/savings_calibration.json')


if __name__ == '__main__':
    main()
