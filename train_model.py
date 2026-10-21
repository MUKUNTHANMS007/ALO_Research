"""Train the ALO models on collected/runs.csv and export them for the API.
Outputs: model/alo_et.joblib (predictor), model/alo_tree.joblib (readable rule view), model/url_profiles.json

Predictor: Extra Trees on 4 page features (total bytes, script bytes, request count, image bytes) + network profile.
Dropping the other 7 of the 11 collected composition features changed nothing on unseen websites (paired grouped CV,
dR2 +0.003, p = 0.93), while removing script or image bytes did hurt. The 11-feature page profile per URL is still stored
because the optimisation-saving estimates use the per-type byte breakdown.
Rule view: a single decision tree on total bytes + network (the readable explanation shown in the dashboard).
Also stored with the predictor: a prediction range per network / speed bucket (calibrated on out-of-fold errors, coverage
checked by cross-fitting) and permutation importance on held-out websites."""
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
ET_FEATURES = ['total_network_bytes', 'script_bytes', 'request_count', 'image_bytes']
FULL_COLUMNS = ET_FEATURES + [f'network_profile_{p}' for p in PROFILES]
GRID = {'max_depth': [3, 4, 5, 6], 'min_samples_leaf': [5, 10, 20]}
# Extra Trees beat the single tree in 13/15 paired CV folds. min_samples_leaf=2 was picked from {2, 4, 8} on an earlier CV
# of the same data, and the 4-feature set from 6 candidate sets on this CV, so scores are slightly optimistic.
ET_PARAMS = dict(n_estimators=300, min_samples_leaf=2, max_features=0.6, random_state=0, n_jobs=1)


def to_frame(rows):
    """rows: list of dicts with FEATURES + 'network_profile' -> model input frame (fixed column order)."""
    df = pd.DataFrame(rows)
    for p in PROFILES:
        df[f'network_profile_{p}'] = (df['network_profile'] == p).astype(int)
    return df[COLUMNS]


def to_frame_full(rows):
    """Like to_frame, but with the ET_FEATURES page features (input of the Extra Trees predictor)."""
    df = pd.DataFrame(rows)
    for p in PROFILES:
        df[f'network_profile_{p}'] = (df['network_profile'] == p).astype(int)
    return df[FULL_COLUMNS]


def tuned_tree(X, y, g):
    return GridSearchCV(DecisionTreeRegressor(random_state=0), GRID, cv=GroupKFold(3), scoring='r2') \
        .fit(X, y, groups=g).best_estimator_


def extra_trees(X, y, g):
    return ExtraTreesRegressor(**ET_PARAMS).fit(X, y)


def repeated_grouped_cv(X, y, g, fit=tuned_tree, reps=3, k=5, seed=0, return_oof=False):
    """Fit (tuning inside the training fold if `fit` does so); score on held-out websites; repeat with different
    fold assignments. Same seed = same folds for every model, so scores are paired.
    return_oof: also return [(out-of-fold prediction array, fold-id array)] per repeat."""
    rng = np.random.default_rng(seed)
    ug = np.unique(g)
    r2s, maes, medaes, oof = [], [], [], []
    for _ in range(reps):
        perm = rng.permutation(ug)
        fold = np.array([{d: i % k for i, d in enumerate(perm)}[v] for v in g])
        pred = np.zeros(len(y))
        for i in range(k):
            tr, te = np.where(fold != i)[0], np.where(fold == i)[0]
            m = fit(X.iloc[tr], y[tr], g[tr])
            p = m.predict(X.iloc[te])
            pred[te] = p
            r2s.append(r2_score(y[te], p))
            maes.append(mean_absolute_error(np.expm1(y[te]), np.expm1(p)))
            medaes.append(median_absolute_error(np.expm1(y[te]), np.expm1(p)))
        oof.append((pred, fold))
    out = float(np.mean(r2s)), float(np.std(r2s)), float(np.mean(maes)), float(np.mean(medaes))
    return (*out, oof) if return_oof else out


# ---- prediction range -------------------------------------------------------------------------------------------
# The tree-vote spread of the ensemble is overconfident (its nominal 80% interval covered 67% of held-out pages), so the
# range is built from held-out errors instead: the 10th..90th percentile of (log1p actual - log1p predicted), per network.
# Slow 3G and Fast 4G get a separate band per predicted-speed bucket (<3 s, 3-8 s, >=8 s); No_Throttle gets one band because
# its bucket bands did not transfer to held-out sites (pages predicted >= 8 s on an unthrottled link are rare, heavy-tailed).
LEVEL = (0.10, 0.90)
SPEED_EDGES_MS = (3000, 8000)
BANDED = ('Slow_3G', 'Fast_4G')


def speed_bucket(pred_ms):
    return int(pred_ms >= SPEED_EDGES_MS[0]) + int(pred_ms >= SPEED_EDGES_MS[1])


def fit_bands(res, pred_ms, nets):
    """res = log1p(actual) - log1p(predicted) out of fold. Returns {network: [[lo, hi] per speed bucket]} in log units."""
    buckets = np.array([speed_bucket(v) for v in pred_ms])
    table = {}
    for n in PROFILES:
        m = nets == n
        whole = np.quantile(res[m], LEVEL)
        row = []
        for b in range(3):
            mb = m & (buckets == b)
            q = np.quantile(res[mb], LEVEL) if n in BANDED and mb.sum() >= 30 else whole
            row.append([round(float(q[0]), 3), round(float(q[1]), 3)])
        table[n] = row
    return table


def apply_band(table, pred_ms, network):
    """(low_ms, high_ms) that should contain the actual load time about 80% of the time."""
    lo, hi = table[network][speed_bucket(pred_ms)]
    z = np.log1p(pred_ms)
    return float(np.expm1(z + lo)), float(np.expm1(z + hi))


def crossfit_coverage(oof, y, nets):
    """Fit the bands on the errors of other websites and test them on held-out websites (the honest coverage)."""
    hits, by_net = [], {n: [] for n in PROFILES}
    for pred, fold in oof:
        res, pms = y - pred, np.expm1(pred)
        for i in np.unique(fold):
            tr, te = fold != i, fold == i
            table = fit_bands(res[tr], pms[tr], nets[tr])
            for idx in np.where(te)[0]:
                lo, hi = apply_band(table, pms[idx], nets[idx])
                ok = lo <= np.expm1(y[idx]) <= hi
                hits.append(ok)
                by_net[nets[idx]].append(ok)
    return {'overall': round(float(np.mean(hits)), 3), **{n: round(float(np.mean(v)), 3) for n, v in by_net.items()}}


def permutation_importance(X, y, g, fit, n_perm=5, k=5, seed=0):
    """Drop in held-out R2 (log scale) when a feature is shuffled, averaged over folds. The network columns are shuffled
    together as one 'network' feature. (Impurity importance over-rates features with many distinct values.)"""
    rng = np.random.default_rng(seed)
    ug = np.unique(g)
    perm = rng.permutation(ug)
    fold = np.array([{d: i % k for i, d in enumerate(perm)}[v] for v in g])
    groups = {c: [c] for c in X.columns if not c.startswith('network_profile_')}
    groups['network'] = [c for c in X.columns if c.startswith('network_profile_')]
    drops = {name: [] for name in groups}
    for i in range(k):
        tr, te = np.where(fold != i)[0], np.where(fold == i)[0]
        m = fit(X.iloc[tr], y[tr], g[tr])
        base = r2_score(y[te], m.predict(X.iloc[te]))
        for name, cols in groups.items():
            d = []
            for _ in range(n_perm):
                Xp = X.iloc[te].copy()
                Xp[cols] = Xp[cols].values[rng.permutation(len(te))]
                d.append(base - r2_score(y[te], m.predict(Xp)))
            drops[name].append(np.mean(d))
    out = [{'feature': name, 'value': round(float(np.mean(v)), 4)} for name, v in drops.items()]
    return sorted(out, key=lambda r: -r['value'])


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

    # Predictor served by the API: Extra Trees on ET_FEATURES + network (same folds as the tree).
    Xf = to_frame_full(d[FEATURES + ['network_profile']].to_dict('records'))
    nets = d.network_profile.values
    e_r2, e_sd, e_mae, e_medae, oof = repeated_grouped_cv(Xf, y, g, fit=extra_trees, return_oof=True)
    pooled_res = np.concatenate([y - p for p, _ in oof])
    pooled_pms = np.concatenate([np.expm1(p) for p, _ in oof])
    pooled_net = np.tile(nets, len(oof))
    table = fit_bands(pooled_res, pooled_pms, pooled_net)
    coverage = crossfit_coverage(oof, y, nets)
    et_metrics = {'r2_log': e_r2, 'r2_sd': e_sd, 'mae_ms': e_mae, 'median_ae_ms': e_medae, 'params': ET_PARAMS,
                  'runs': metrics['runs'], 'urls': metrics['urls'], 'inputs': ET_FEATURES + ['network profile'],
                  'eval': 'repeated grouped CV (3x5), same folds as the single tree',
                  'comparison': [{'model': 'Single decision tree (rule view)', 'mae_ms': mae, 'r2': r2},
                                 {'model': 'Extra Trees, 4 features (predictor)', 'mae_ms': e_mae, 'r2': e_r2}],
                  'importance': permutation_importance(Xf, y, g, extra_trees),
                  'interval': {'level': 0.8, 'table': table, 'coverage_crossfit': coverage,
                               'note': 'band per network x predicted speed (<3 s, 3-8 s, >=8 s) for Slow 3G and Fast 4G; '
                                       'one band for No_Throttle. Log offsets of the prediction (log1p scale).'}}
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
    print('extra trees', {k: v for k, v in et_metrics.items() if k not in ('comparison', 'interval', 'importance')})
    print('importance (held-out R2 drop):', et_metrics['importance'])
    print('range table:', table)
    print('cross-fitted coverage of the 80% range:', coverage)
    print('depth', model.get_depth(), 'leaves', model.get_n_leaves(),
          {c: round(float(v), 3) for c, v in zip(COLUMNS, model.feature_importances_)})


if __name__ == '__main__':
    main()
