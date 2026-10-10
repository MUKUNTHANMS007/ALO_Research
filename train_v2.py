"""Model comparison on new_preprocessed.csv (cleaned, leakage removed), domain-grouped CV.
Run: python train_v2.py"""
import warnings

import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.tree import DecisionTreeRegressor, export_text

warnings.filterwarnings('ignore')

d = pd.read_csv('new_preprocessed.csv')
n0 = len(d)
d = d[d.navigation_status == 'success']
d = d[(d.request_count > 1) & (d.total_network_bytes > 0) & (d.page_load_time_ms > 100)]
print(f'runs {n0} -> {len(d)}  domains {d.url_domain.nunique()}')

# Page composition + network profile only. Excluded as leakage/outcomes: request durations, task/script/layout
# durations, failure/http-error counts and rates, heap, rss, cpu, dom_content_loaded, timing-derived columns.
comp = (['request_count', 'total_network_bytes', 'dom_node_count']
        + [c for c in d.columns if c.startswith('request_count_type_') or c.startswith('request_bytes_type_')
           or c.startswith('request_count_method_') or c.startswith('request_count_mime_')]
        + ['document_bytes', 'script_bytes', 'stylesheet_bytes', 'image_bytes', 'font_bytes', 'xhr_fetch_bytes',
           'media_bytes', 'other_bytes'])
comp = [c for c in dict.fromkeys(comp) if d[c].nunique() > 1]
X = pd.get_dummies(d[comp + ['network_profile']], columns=['network_profile']).astype(float)
y = np.log1p(d.page_load_time_ms.values)
g = d.url_domain.values
print('features:', X.shape[1])

# nested-style: tune inside each training fold, score on held-out domains
cv = GroupKFold(5)
cands = {
    'Median baseline': (DummyRegressor(strategy='median'), None),
    'Decision tree (tuned)': (DecisionTreeRegressor(random_state=0),
                              {'max_depth': [3, 4, 5, 6], 'min_samples_leaf': [5, 10, 20]}),
    'Random forest': (RandomForestRegressor(300, min_samples_leaf=4, max_features=0.6, random_state=0, n_jobs=1), None),
    'Extra trees': (ExtraTreesRegressor(300, min_samples_leaf=4, max_features=0.6, random_state=0, n_jobs=1), None),
    'HistGB': (HistGradientBoostingRegressor(max_iter=150, max_leaf_nodes=8, learning_rate=0.05,
                                             l2_regularization=1.0, random_state=0), None),
}
rows = []
for name, (est, grid) in cands.items():
    r2s, maes, rmses = [], [], []
    for tr, te in cv.split(X, y, g):
        m = est
        if grid:
            m = GridSearchCV(est, grid, cv=GroupKFold(4), scoring='r2').fit(X.iloc[tr], y[tr], groups=g[tr]).best_estimator_
        else:
            from sklearn.base import clone
            m = clone(est).fit(X.iloc[tr], y[tr])
        p = m.predict(X.iloc[te])
        r2s.append(r2_score(y[te], p))
        maes.append(mean_absolute_error(np.expm1(y[te]), np.expm1(p)))
        rmses.append(np.sqrt(np.mean((np.expm1(y[te]) - np.expm1(p)) ** 2)))
    rows.append((name, np.mean(maes), np.std(maes), np.mean(rmses), np.mean(r2s), np.std(r2s)))
res = pd.DataFrame(rows, columns=['model', 'MAE_ms', 'MAE_sd', 'RMSE_ms', 'R2_log', 'R2_sd']).set_index('model')
print(res.sort_values('MAE_ms').round(3).to_string())

# final tree on all data for the readable rules
gs = GridSearchCV(DecisionTreeRegressor(random_state=0),
                  {'max_depth': [3, 4, 5, 6], 'min_samples_leaf': [5, 10, 20]}, cv=cv, scoring='r2').fit(X, y, groups=g)
print('final tree params', gs.best_params_)
imp = pd.Series(gs.best_estimator_.feature_importances_, X.columns).sort_values(ascending=False)
print(imp.head(8).round(3).to_string())
res.to_csv('model_comparison_v2.csv')
