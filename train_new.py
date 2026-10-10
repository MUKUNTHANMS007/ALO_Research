"""Evaluate ALO models on collected/runs.csv (baseline, cold cache, cpu_1x). Does NOT overwrite the exported model."""
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.tree import DecisionTreeRegressor

r = pd.read_csv('collected/runs.csv')
d = r[(r.variant == 'baseline') & (r.cpu_profile == 'cpu_1x') & (r.cache_state == 'cold')]
d = d[(d.request_count > 1) & (d.total_network_bytes > 0) & (d.page_load_time_ms > 100)].reset_index(drop=True)
d['dom'] = d.url.str.replace('https://', '').str.replace('http://', '').str.rstrip('/')
print('rows', len(d), 'sites', d.dom.nunique(), d.network_profile.value_counts().to_dict())
SMALL = ['request_count', 'total_network_bytes', 'document_bytes', 'script_bytes', 'stylesheet_bytes', 'image_bytes',
         'font_bytes', 'xhr_fetch_bytes', 'media_bytes', 'other_bytes', 'dom_node_count']
def X(cols): return pd.get_dummies(d[cols + ['network_profile']], columns=['network_profile']).astype(float)
y = np.log1p(d.page_load_time_ms.values); ms = d.page_load_time_ms.values; g = d.dom.values
G = {'max_depth': [3, 4, 5, 6], 'min_samples_leaf': [5, 10, 20]}
def tree(Xt, yt, gt): return GridSearchCV(DecisionTreeRegressor(random_state=0), G, cv=GroupKFold(3), scoring='r2').fit(Xt, yt, groups=gt).best_estimator_
def et(Xt, yt, gt): return ExtraTreesRegressor(200, min_samples_leaf=4, max_features=0.6, random_state=0, n_jobs=1).fit(Xt, yt)
def cv(mk, Xd, oof=False):
    rng = np.random.default_rng(0); ug = np.unique(g); out = []; P = np.full(len(y), np.nan)
    for rep in range(3):
        perm = rng.permutation(ug); fo = {k: i % 5 for i, k in enumerate(perm)}; f = np.array([fo[k] for k in g])
        for i in range(5):
            tr, te = np.where(f != i)[0], np.where(f == i)[0]
            m = mk(Xd.iloc[tr], y[tr], g[tr]); p = m.predict(Xd.iloc[te])
            out.append((r2_score(y[te], p), mean_absolute_error(ms[te], np.expm1(p))))
            if rep == 0: P[te] = p
    a = np.array(out); s = f'R2 {a[:,0].mean():.3f} ±{a[:,0].std():.3f}  MAE {a[:,1].mean()/1000:.2f}s'
    return (s, P) if oof else s
print('median baseline MAE', round(np.mean(np.abs(ms - np.median(ms))) / 1000, 2), 's')
s, P = cv(tree, X(['total_network_bytes']), True); print('tree bytes+net      ', s)
print('tree 11 feats       ', cv(tree, X(SMALL)))
print('ET   11 feats       ', cv(et, X(SMALL)))
print('ET   bytes only     ', cv(et, X(['total_network_bytes'])))
Xt = X(SMALL + ['ttfb_ms']); print('ET   11 + TTFB      ', cv(et, Xt))
print('tree bytes+TTFB     ', cv(tree, X(['total_network_bytes', 'ttfb_ms'])))
for n in d.network_profile.unique():
    m = (d.network_profile == n).values & ~np.isnan(P)
    print(f'  tree per-net {n:12s} n={m.sum()} R2={r2_score(y[m], P[m]):.3f} MAE={mean_absolute_error(ms[m], np.expm1(P[m]))/1000:.2f}s')
print('classes <3s:', (ms < 3000).sum(), ' 3-8s:', ((ms >= 3000) & (ms < 8000)).sum(), ' >=8s:', (ms >= 8000).sum())
# old -> new transfer
o = pd.read_csv('new_preprocessed.csv'); o = o[(o.navigation_status == 'success') & (o.request_count > 1) & (o.total_network_bytes > 0) & (o.page_load_time_ms > 100)]
Xo = pd.get_dummies(o[['total_network_bytes', 'network_profile']], columns=['network_profile']).astype(float)
m = tree(Xo, np.log1p(o.page_load_time_ms.values), o.url_domain.values); Xn = X(['total_network_bytes'])[Xo.columns]
p = m.predict(Xn); print(f'OLD-trained tree on NEW data: R2 {r2_score(y,p):.3f}  MAE {mean_absolute_error(ms,np.expm1(p))/1000:.2f}s')
