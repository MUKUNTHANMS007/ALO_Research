"""Full ALO training / evaluation suite. Writes reports/results.json and reports/fig_*.png.
Run from the project root:  python train_all.py
Evaluation is always grouped by website domain (a site never appears in both train and test),
repeated 3x with different random fold assignments, 5 folds each."""
import json
import os
import time
import warnings

os.environ.setdefault('LOKY_MAX_CPU_COUNT', '4')
warnings.filterwarnings('ignore')

import joblib
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import (ExtraTreesClassifier, ExtraTreesRegressor, HistGradientBoostingRegressor,
                              RandomForestRegressor)
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, mean_absolute_error, median_absolute_error,
                             r2_score)
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.base import clone
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

OUT = 'reports'
os.makedirs(OUT, exist_ok=True)
RES = {}
SEED, K, REPS = 0, 5, 3
T0 = time.time()


def log(msg):
    print(f'[{time.time() - T0:6.0f}s] {msg}', flush=True)


# ------------------------------------------------------------------ data
raw = pd.read_csv('new_preprocessed.csv')
RES['data'] = {'rows_total': int(len(raw)), 'domains_total': int(raw.url_domain.nunique()),
               'navigation_status': raw.navigation_status.value_counts().to_dict()}
d = raw[raw.navigation_status == 'success']
n_success = len(d)
d = d[(d.request_count > 1) & (d.total_network_bytes > 0) & (d.page_load_time_ms > 100)].reset_index(drop=True)
RES['data'].update(rows_success=int(n_success), rows_kept=int(len(d)), domains_kept=int(d.url_domain.nunique()),
                   rows_dropped_broken=int(n_success - len(d)),
                   runs_per_network=d.network_profile.value_counts().to_dict(),
                   load_time_s_by_network={k: {q: round(float(v.quantile(p)) / 1000, 2) for q, p in
                                               (('p10', .1), ('median', .5), ('p90', .9))}
                                           for k, v in d.groupby('network_profile').page_load_time_ms})
# how strongly does size explain load time inside each network profile? (Spearman)
RES['data']['spearman_bytes_vs_load_by_network'] = {
    k: round(float(g.total_network_bytes.corr(g.page_load_time_ms, method='spearman')), 3)
    for k, g in d.groupby('network_profile')}
RES['data']['constant_columns'] = [c for c in raw.columns if raw[c].nunique() <= 1]
log(f"data: {len(raw)} raw -> {len(d)} kept, {d.url_domain.nunique()} domains")

SMALL = ['request_count', 'total_network_bytes', 'document_bytes', 'script_bytes', 'stylesheet_bytes', 'image_bytes',
         'font_bytes', 'xhr_fetch_bytes', 'media_bytes', 'other_bytes', 'dom_node_count']
COMPACT = ['total_network_bytes', 'script_bytes', 'request_count', 'dom_node_count']
ALL51 = [c for c in dict.fromkeys(
    ['request_count', 'total_network_bytes', 'dom_node_count']
    + [c for c in d.columns if c.startswith(('request_count_type_', 'request_bytes_type_', 'request_count_method_',
                                             'request_count_mime_'))]
    + SMALL[2:]) if d[c].nunique() > 1]
LEAKY = SMALL + ['request_duration_mean_ms', 'request_duration_p95_ms', 'task_duration_ms', 'script_duration_ms',
                 'request_failed_count']


def design(cols):
    return pd.get_dummies(d[cols + ['network_profile']], columns=['network_profile']).astype(float)


y = np.log1p(d.page_load_time_ms.values)
y_ms = d.page_load_time_ms.values
groups = d.url_domain.values
profile = d.network_profile.values


def splits(reps=REPS, k=K, seed=SEED):
    """Repeated grouped K-fold: shuffle domains, assign round-robin to folds."""
    rng = np.random.default_rng(seed)
    ug = np.unique(groups)
    for r in range(reps):
        perm = rng.permutation(ug)
        fold_of = {g: i % k for i, g in enumerate(perm)}
        f = np.array([fold_of[g] for g in groups])
        for i in range(k):
            yield r, np.where(f != i)[0], np.where(f == i)[0]


TREE_GRID = {'max_depth': [3, 4, 5, 6], 'min_samples_leaf': [5, 10, 20]}


def tuned_tree(X, tr):
    gs = GridSearchCV(DecisionTreeRegressor(random_state=0), TREE_GRID, cv=GroupKFold(3), scoring='r2')
    return gs.fit(X.iloc[tr], y[tr], groups=groups[tr]).best_estimator_


def evaluate(make, X, name=None, keep_oof=False):
    """make(X, train_idx) -> fitted model. Returns metric means/SDs over all folds; optional OOF (rep 0)."""
    rows, oof = [], np.full(len(y), np.nan)
    for r, tr, te in splits():
        m = make(X, tr)
        p = m.predict(X.iloc[te])
        a, b = y_ms[te], np.expm1(p)
        rows.append(dict(r2=r2_score(y[te], p), mae=mean_absolute_error(a, b),
                         rmse=float(np.sqrt(np.mean((a - b) ** 2))), medae=median_absolute_error(a, b),
                         mdape=float(np.median(np.abs(a - b) / a)) * 100))
        if r == 0:
            oof[te] = p
    df = pd.DataFrame(rows)
    out = {k: [round(float(df[k].mean()), 4), round(float(df[k].std()), 4)] for k in df.columns}
    if name:
        log(f"{name:34s} R2={out['r2'][0]:.3f}  MAE={out['mae'][0]:.0f} ms")
    return (out, oof) if keep_oof else out


def fit_plain(est):
    return lambda X, tr: clone(est).fit(X.iloc[tr], y[tr])


# ------------------------------------------------------------------ E2 model comparison
log('E2 model comparison (11 features + network profile)')
X11 = design(SMALL)
models = {
    'Median baseline': fit_plain(DummyRegressor(strategy='median')),
    'Linear regression': fit_plain(make_pipeline(StandardScaler(), LinearRegression())),
    'Ridge (alpha=10)': fit_plain(make_pipeline(StandardScaler(), Ridge(alpha=10))),
    'Decision tree (tuned)': tuned_tree,
    'Random forest': fit_plain(RandomForestRegressor(200, min_samples_leaf=4, max_features=0.6, random_state=0, n_jobs=1)),
    'Extra trees': fit_plain(ExtraTreesRegressor(200, min_samples_leaf=4, max_features=0.6, random_state=0, n_jobs=1)),
    'Hist gradient boosting': fit_plain(HistGradientBoostingRegressor(max_iter=150, max_leaf_nodes=8, learning_rate=0.05,
                                                                      l2_regularization=1.0, random_state=0)),
}
RES['comparison'], tree_oof = {}, None
for name, mk in models.items():
    if name == 'Decision tree (tuned)':
        RES['comparison'][name], tree_oof = evaluate(mk, X11, name, keep_oof=True)
    else:
        RES['comparison'][name] = evaluate(mk, X11, name)

# ------------------------------------------------------------------ E3 ablation / leakage
log('E3 feature-set ablation (tree + extra trees)')
sets = {'Network profile only': ([], 'network only'),
        'Total bytes + network': (['total_network_bytes'], 'total bytes'),
        'Compact (4 features) + network': (COMPACT, 'compact'),
        'Small (11 features) + network': (SMALL, 'small'),
        f'All composition ({len(ALL51)}) + network': (ALL51, 'all'),
        'Small + outcome columns (LEAKY)': (LEAKY, 'leaky')}
RES['ablation'] = {}
for label, (cols, _) in sets.items():
    X = design(cols) if cols else pd.get_dummies(d[['network_profile']]).astype(float)
    RES['ablation'][label] = {
        'n_features': int(X.shape[1]),
        'Decision tree (tuned)': evaluate(tuned_tree, X, f'abl tree  | {label}'),
        'Extra trees': evaluate(fit_plain(ExtraTreesRegressor(150, min_samples_leaf=4, max_features=0.6,
                                                              random_state=0, n_jobs=1)), X, f'abl ET    | {label}')}

# ------------------------------------------------------------------ E4 per-network
log('E4 per-network error (tree out-of-fold)')
ok = ~np.isnan(tree_oof)
RES['per_network'] = {}
for pf in sorted(set(profile)):
    m = ok & (profile == pf)
    RES['per_network'][pf] = {'runs': int(m.sum()), 'mean_actual_s': round(float(y_ms[m].mean()) / 1000, 2),
                              'mae_s': round(float(mean_absolute_error(y_ms[m], np.expm1(tree_oof[m]))) / 1000, 2),
                              'r2_log': round(float(r2_score(y[m], tree_oof[m])), 3),
                              'mdape_pct': round(float(np.median(np.abs(y_ms[m] - np.expm1(tree_oof[m])) / y_ms[m])) * 100, 1)}

# ------------------------------------------------------------------ E5 depth sensitivity
log('E5 tree depth sensitivity')
RES['depth'] = {}
for dep in [2, 3, 4, 5, 6, 8, 10, None]:
    RES['depth'][str(dep)] = evaluate(fit_plain(DecisionTreeRegressor(max_depth=dep, min_samples_leaf=10, random_state=0)), X11)
    log(f"depth={dep}: R2={RES['depth'][str(dep)]['r2'][0]:.3f} MAE={RES['depth'][str(dep)]['mae'][0]:.0f}")

# ------------------------------------------------------------------ E6 log vs raw target
log('E6 log vs raw target (tree)')
def tree_raw(X, tr):
    gs = GridSearchCV(DecisionTreeRegressor(random_state=0), TREE_GRID, cv=GroupKFold(3), scoring='r2')
    gs.fit(X.iloc[tr], y_ms[tr], groups=groups[tr])
    class W:  # predict in log space so the same metric code applies
        def predict(self, Xt): return np.log1p(np.maximum(gs.predict(Xt), 1))
    return W()
RES['target_choice'] = {'log1p target': RES['comparison']['Decision tree (tuned)'],
                        'raw milliseconds target': evaluate(tree_raw, X11, 'raw-ms target tree')}

# ------------------------------------------------------------------ E7 speed classes
log('E7 speed-class classification')
cls = np.where(y_ms < 3000, 0, np.where(y_ms < 8000, 1, 2))
names = ['fast (<3 s)', 'medium (3-8 s)', 'slow (>=8 s)']
RES['classes'] = {'counts': {names[i]: int((cls == i).sum()) for i in range(3)}}
def class_eval(make, label):
    accs, f1s, cm = [], [], np.zeros((3, 3), int)
    for r, tr, te in splits():
        m = make(tr)
        p = m.predict(X11.iloc[te])
        accs.append(accuracy_score(cls[te], p)); f1s.append(f1_score(cls[te], p, average='macro'))
        if r == 0:
            cm += confusion_matrix(cls[te], p, labels=[0, 1, 2])
    log(f'{label:28s} acc={np.mean(accs):.3f} macroF1={np.mean(f1s):.3f}')
    return {'accuracy': [round(float(np.mean(accs)), 3), round(float(np.std(accs)), 3)],
            'macro_f1': [round(float(np.mean(f1s)), 3), round(float(np.std(f1s)), 3)], 'confusion_rep0': cm.tolist()}
def tuned_clf(tr):
    gs = GridSearchCV(DecisionTreeClassifier(random_state=0, class_weight='balanced'),
                      {'max_depth': [3, 4, 5, 6], 'min_samples_leaf': [5, 10, 20]}, cv=GroupKFold(3), scoring='f1_macro')
    return gs.fit(X11.iloc[tr], cls[tr], groups=groups[tr]).best_estimator_
RES['classes']['Majority baseline'] = class_eval(lambda tr: DummyClassifier(strategy='most_frequent').fit(X11.iloc[tr], cls[tr]), 'majority baseline')
RES['classes']['Decision tree (tuned)'] = class_eval(tuned_clf, 'decision tree classifier')
RES['classes']['Extra trees'] = class_eval(lambda tr: ExtraTreesClassifier(200, min_samples_leaf=3, class_weight='balanced', random_state=0, n_jobs=1).fit(X11.iloc[tr], cls[tr]), 'extra trees classifier')

# ------------------------------------------------------------------ E8 learning curve
log('E8 learning curve (share of training domains)')
RES['learning_curve'] = {}
rng = np.random.default_rng(1)
for frac in [0.2, 0.4, 0.6, 0.8, 1.0]:
    def sub(X, tr, est=None, frac=frac):
        ug = np.unique(groups[tr]); keep = set(rng.choice(ug, max(3, int(len(ug) * frac)), replace=False))
        tr2 = tr[np.isin(groups[tr], list(keep))]
        return DecisionTreeRegressor(max_depth=5, min_samples_leaf=max(3, int(10 * frac)), random_state=0).fit(X.iloc[tr2], y[tr2])
    def sub_et(X, tr, frac=frac):
        ug = np.unique(groups[tr]); keep = set(rng.choice(ug, max(3, int(len(ug) * frac)), replace=False))
        tr2 = tr[np.isin(groups[tr], list(keep))]
        return ExtraTreesRegressor(150, min_samples_leaf=4, max_features=0.6, random_state=0, n_jobs=1).fit(X.iloc[tr2], y[tr2])
    RES['learning_curve'][str(frac)] = {'Decision tree': evaluate(sub, X11), 'Extra trees': evaluate(sub_et, X11)}
    t = RES['learning_curve'][str(frac)]
    log(f"train domains {int(frac*100)}%: tree R2={t['Decision tree']['r2'][0]:.3f}  ET R2={t['Extra trees']['r2'][0]:.3f}")

# ------------------------------------------------------------------ E9 worst domains
dom = pd.DataFrame({'domain': groups, 'actual': y_ms, 'pred': np.expm1(tree_oof), 'net': profile})[ok]
dom['err'] = (dom.pred - dom.actual).abs()
worst = dom.groupby('domain').agg(mean_actual_s=('actual', lambda s: s.mean() / 1000), mae_s=('err', lambda s: s.mean() / 1000),
                                  bias_s=('pred', 'mean')).reset_index()
worst['bias_s'] = (worst.bias_s / 1000 - worst.mean_actual_s).round(2)
RES['worst_domains'] = worst.sort_values('mae_s', ascending=False).head(8).round(2).to_dict('records')
RES['best_domains'] = worst.sort_values('mae_s').head(5).round(2).to_dict('records')

# ------------------------------------------------------------------ final models
log('final models')
Xall = X11
gs = GridSearchCV(DecisionTreeRegressor(random_state=0), TREE_GRID, cv=GroupKFold(5), scoring='r2').fit(Xall, y, groups=groups)
tree = gs.best_estimator_
et = ExtraTreesRegressor(300, min_samples_leaf=4, max_features=0.6, random_state=0, n_jobs=1).fit(Xall, y)
os.makedirs('model', exist_ok=True)
joblib.dump({'model': et, 'columns': list(Xall.columns)}, 'model/alo_extratrees.joblib')
RES['final'] = {'tree_params': gs.best_params_, 'tree_depth': int(tree.get_depth()), 'tree_leaves': int(tree.get_n_leaves()),
                'tree_importance': {c: round(float(v), 3) for c, v in
                                    sorted(zip(Xall.columns, tree.feature_importances_), key=lambda x: -x[1]) if v > 0},
                'et_importance': {c: round(float(v), 3) for c, v in
                                  sorted(zip(Xall.columns, et.feature_importances_), key=lambda x: -x[1])[:8]}}

# ------------------------------------------------------------------ figures
log('figures')
BLUE, SLATE, LIGHT = '#0284c7', '#64748b', '#7dd3fc'
plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})

c = RES['comparison']; order = sorted(c, key=lambda k: c[k]['mae'][0])
fig, ax = plt.subplots(figsize=(7.5, 3.8))
ax.barh(order[::-1], [c[k]['mae'][0] / 1000 for k in order[::-1]], xerr=[c[k]['mae'][1] / 1000 for k in order[::-1]],
        color=[BLUE if k.startswith('Decision') else LIGHT for k in order[::-1]])
ax.set_xlabel('Mean absolute error (s) on unseen websites; lower is better'); fig.tight_layout()
fig.savefig(f'{OUT}/fig_model_comparison.png', dpi=140); plt.close(fig)

fig, ax = plt.subplots(figsize=(5.2, 5))
cols = {'No_Throttle': '#16a34a', 'Fast_4G': BLUE, 'Slow_3G': '#d97706'}
for pf in cols:
    m = ok & (profile == pf)
    ax.scatter(y_ms[m] / 1000, np.expm1(tree_oof[m]) / 1000, s=14, alpha=.6, c=cols[pf], label=pf.replace('_', ' '))
ax.plot([0.3, 70], [0.3, 70], c=SLATE, lw=1, ls='--'); ax.set_xscale('log'); ax.set_yscale('log')
ax.set_xlabel('Actual load time (s)'); ax.set_ylabel('Predicted (s), unseen site'); ax.legend(frameon=False)
fig.tight_layout(); fig.savefig(f'{OUT}/fig_pred_vs_actual.png', dpi=140); plt.close(fig)

lc = RES['learning_curve']; xs = [int(float(k) * 100) for k in lc]
fig, ax = plt.subplots(figsize=(6, 3.8))
for nm, col in (('Decision tree', BLUE), ('Extra trees', SLATE)):
    ys = [lc[k][nm]['r2'][0] for k in lc]; sd = [lc[k][nm]['r2'][1] for k in lc]
    ax.errorbar(xs, ys, yerr=sd, marker='o', c=col, label=nm, capsize=3)
ax.set_xlabel('% of training websites used'); ax.set_ylabel('R2 on unseen sites (log time)'); ax.legend(frameon=False)
fig.tight_layout(); fig.savefig(f'{OUT}/fig_learning_curve.png', dpi=140); plt.close(fig)

ab = RES['ablation']; labs = list(ab)
fig, ax = plt.subplots(figsize=(8, 4))
w = 0.38; x = np.arange(len(labs))
ax.bar(x - w / 2, [ab[k]['Decision tree (tuned)']['r2'][0] for k in labs], w, label='Decision tree', color=BLUE)
ax.bar(x + w / 2, [ab[k]['Extra trees']['r2'][0] for k in labs], w, label='Extra trees', color=LIGHT)
ax.set_xticks(x); ax.set_xticklabels([l.replace(' + ', '\n+ ').replace(' (', '\n(') for l in labs], fontsize=7.5)
ax.set_ylabel('R2 on unseen sites'); ax.legend(frameon=False); fig.tight_layout()
fig.savefig(f'{OUT}/fig_ablation.png', dpi=140); plt.close(fig)

imp = RES['final']['tree_importance']; ks = list(imp)[:8]
fig, ax = plt.subplots(figsize=(6.5, 3.6))
ax.barh([k.replace('network_profile_', 'network: ').replace('_', ' ') for k in ks][::-1], [imp[k] for k in ks][::-1], color=BLUE)
ax.set_xlabel('Share of the tree\'s splitting power'); fig.tight_layout()
fig.savefig(f'{OUT}/fig_importance.png', dpi=140); plt.close(fig)

json.dump(RES, open(f'{OUT}/results.json', 'w'), indent=1, default=str)
log('done -> reports/results.json')
