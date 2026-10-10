"""Train the ALO decision tree on new_preprocessed.csv and export it for the API.
Outputs: model/alo_tree.joblib, model/url_profiles.json

The tree uses only total page bytes + network profile: in the ablation (reports/results.json) this beat the
11- and 48-feature trees on unseen websites. The per-type byte breakdown is still stored per URL because the
optimisation-saving estimates need it, but the tree does not split on it."""
import json
import os
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, median_absolute_error, r2_score
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.tree import DecisionTreeRegressor

warnings.filterwarnings('ignore')

FEATURES = ['request_count', 'total_network_bytes', 'document_bytes', 'script_bytes', 'stylesheet_bytes',
            'image_bytes', 'font_bytes', 'xhr_fetch_bytes', 'media_bytes', 'other_bytes', 'dom_node_count']
MODEL_INPUTS = ['total_network_bytes']
PROFILES = ['Fast_4G', 'No_Throttle', 'Slow_3G']
COLUMNS = MODEL_INPUTS + [f'network_profile_{p}' for p in PROFILES]
GRID = {'max_depth': [3, 4, 5, 6], 'min_samples_leaf': [5, 10, 20]}


def to_frame(rows):
    """rows: list of dicts with FEATURES + 'network_profile' -> model input frame (fixed column order)."""
    df = pd.DataFrame(rows)
    for p in PROFILES:
        df[f'network_profile_{p}'] = (df['network_profile'] == p).astype(int)
    return df[COLUMNS]


def repeated_grouped_cv(X, y, g, reps=3, k=5, seed=0):
    """Tune inside each training fold; score on held-out websites; repeat with different fold assignments."""
    rng = np.random.default_rng(seed)
    ug = np.unique(g)
    r2s, maes, medaes = [], [], []
    for _ in range(reps):
        perm = rng.permutation(ug)
        fold = np.array([{d: i % k for i, d in enumerate(perm)}[v] for v in g])
        for i in range(k):
            tr, te = np.where(fold != i)[0], np.where(fold == i)[0]
            m = GridSearchCV(DecisionTreeRegressor(random_state=0), GRID, cv=GroupKFold(3), scoring='r2') \
                .fit(X.iloc[tr], y[tr], groups=g[tr]).best_estimator_
            p = m.predict(X.iloc[te])
            r2s.append(r2_score(y[te], p))
            maes.append(mean_absolute_error(np.expm1(y[te]), np.expm1(p)))
            medaes.append(median_absolute_error(np.expm1(y[te]), np.expm1(p)))
    return float(np.mean(r2s)), float(np.std(r2s)), float(np.mean(maes)), float(np.mean(medaes))


def main():
    # Source: collected/runs.csv (baseline, cold cache, 1x CPU). The first dataset (new_preprocessed.csv) is only
    # used to keep page profiles for URLs the new collector has not covered.
    d = pd.read_csv('collected/runs.csv')
    d = d[(d.variant == 'baseline') & (d.cpu_profile == 'cpu_1x') & (d.cache_state == 'cold')]
    d = d[d.navigation_status == 'success'].copy()
    d['url_domain'] = d.url.str.replace(r'^https?://', '', regex=True).str.rstrip('/').str.lower()
    d = d[(d.request_count > 1) & (d.total_network_bytes > 0) & (d.page_load_time_ms > 100)]
    X = to_frame(d[FEATURES + ['network_profile']].to_dict('records'))
    y = np.log1p(d.page_load_time_ms.values)
    g = d.url_domain.values

    r2, r2_sd, mae, medae = repeated_grouped_cv(X, y, g)
    gs = GridSearchCV(DecisionTreeRegressor(random_state=0), GRID, cv=GroupKFold(5), scoring='r2').fit(X, y, groups=g)
    metrics = {'r2_log': r2, 'r2_sd': r2_sd, 'mae_ms': mae, 'median_ae_ms': medae, 'params': gs.best_params_,
               'runs': int(len(d)), 'urls': int(d.url.nunique()), 'inputs': MODEL_INPUTS + ['network profile'],
               'eval': 'repeated grouped CV (3x5), tuned inside each training fold'}
    model = gs.best_estimator_.fit(X, y)

    os.makedirs('model', exist_ok=True)
    joblib.dump({'model': model, 'columns': COLUMNS, 'metrics': metrics}, 'model/alo_tree.joblib')

    # page composition per URL (median across its runs); used because the API only receives a URL
    prof = d.groupby('url')[FEATURES].median().round().astype(int)
    old = pd.read_csv('new_preprocessed.csv')
    old = old[(old.navigation_status == 'success') & (old.request_count > 1) & (old.total_network_bytes > 0)]
    oprof = old.groupby('url')[FEATURES].median().round().astype(int)
    out = {'_global': d[FEATURES].median().round().astype(int).to_dict(),
           **{u.rstrip('/').lower(): r.to_dict() for u, r in oprof.iterrows()},
           **{u.rstrip('/').lower(): r.to_dict() for u, r in prof.iterrows()}}
    json.dump(out, open('model/url_profiles.json', 'w'))
    print('saved model + profiles for', len(prof), 'urls;', metrics)
    print('depth', model.get_depth(), 'leaves', model.get_n_leaves(),
          {c: round(float(v), 3) for c, v in zip(COLUMNS, model.feature_importances_)})


if __name__ == '__main__':
    main()
