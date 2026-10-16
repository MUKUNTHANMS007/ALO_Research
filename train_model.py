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
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, median_absolute_error, r2_score
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.tree import DecisionTreeRegressor

warnings.filterwarnings('ignore')

FEATURES = ['request_count', 'total_network_bytes', 'document_bytes', 'script_bytes', 'stylesheet_bytes',
            'image_bytes', 'font_bytes', 'xhr_fetch_bytes', 'media_bytes', 'other_bytes', 'dom_node_count']
MODEL_INPUTS = ['total_network_bytes']
PROFILES = ['Fast_4G', 'No_Throttle', 'Slow_3G']
COLUMNS = MODEL_INPUTS + [f'network_profile_{p}' for p in PROFILES]
FULL_COLUMNS = FEATURES + [f'network_profile_{p}' for p in PROFILES]
GRID = {'max_depth': [3, 4, 5, 6], 'min_samples_leaf': [5, 10, 20]}
# Extra Trees on all 11 features: better than the single tree in 15/15 paired CV folds (R2 +0.05). min_samples_leaf=2 was
# picked from {2, 4, 8} on that same CV, so its score is slightly optimistic.
ET_PARAMS = dict(n_estimators=300, min_samples_leaf=2, max_features=0.6, random_state=0, n_jobs=1)


def to_frame(rows):
    """rows: list of dicts with FEATURES + 'network_profile' -> model input frame (fixed column order)."""
    df = pd.DataFrame(rows)
    for p in PROFILES:
        df[f'network_profile_{p}'] = (df['network_profile'] == p).astype(int)
    return df[COLUMNS]


def to_frame_full(rows):
    """Like to_frame, but with all 11 page-composition features (input of the Extra Trees predictor)."""
    df = pd.DataFrame(rows)
    for p in PROFILES:
        df[f'network_profile_{p}'] = (df['network_profile'] == p).astype(int)
    return df[FULL_COLUMNS]


def tuned_tree(X, y, g):
    return GridSearchCV(DecisionTreeRegressor(random_state=0), GRID, cv=GroupKFold(3), scoring='r2') \
        .fit(X, y, groups=g).best_estimator_


def extra_trees(X, y, g):
    return ExtraTreesRegressor(**ET_PARAMS).fit(X, y)


def repeated_grouped_cv(X, y, g, fit=tuned_tree, reps=3, k=5, seed=0):
    """Fit (tuning inside the training fold if `fit` does so); score on held-out websites; repeat with different
    fold assignments. Same seed = same folds for every model, so scores are paired."""
    rng = np.random.default_rng(seed)
    ug = np.unique(g)
    r2s, maes, medaes = [], [], []
    for _ in range(reps):
        perm = rng.permutation(ug)
        fold = np.array([{d: i % k for i, d in enumerate(perm)}[v] for v in g])
        for i in range(k):
            tr, te = np.where(fold != i)[0], np.where(fold == i)[0]
            m = fit(X.iloc[tr], y[tr], g[tr])
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

    # Predictor served by the API: Extra Trees on all 11 composition features + network (same folds as the tree).
    Xf = to_frame_full(d[FEATURES + ['network_profile']].to_dict('records'))
    e_r2, e_sd, e_mae, e_medae = repeated_grouped_cv(Xf, y, g, fit=extra_trees)
    et_metrics = {'r2_log': e_r2, 'r2_sd': e_sd, 'mae_ms': e_mae, 'median_ae_ms': e_medae, 'params': ET_PARAMS,
                  'runs': metrics['runs'], 'urls': metrics['urls'], 'inputs': FEATURES + ['network profile'],
                  'eval': 'repeated grouped CV (3x5), same folds as the single tree',
                  'comparison': [{'model': 'Single decision tree (rule view)', 'mae_ms': mae, 'r2': r2},
                                 {'model': 'Extra Trees, 11 features (predictor)', 'mae_ms': e_mae, 'r2': e_r2}]}
    et_model = extra_trees(Xf, y, g)

    os.makedirs('model', exist_ok=True)
    joblib.dump({'model': model, 'columns': COLUMNS, 'metrics': metrics}, 'model/alo_tree.joblib')
    joblib.dump({'model': et_model, 'columns': FULL_COLUMNS, 'metrics': et_metrics}, 'model/alo_et.joblib', compress=3)

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
    print('extra trees', {k: v for k, v in et_metrics.items() if k != 'comparison'})
    print('depth', model.get_depth(), 'leaves', model.get_n_leaves(),
          {c: round(float(v), 3) for c, v in zip(COLUMNS, model.feature_importances_)})


if __name__ == '__main__':
    main()
