import pandas as pd, numpy as np
from sklearn.tree import DecisionTreeRegressor, export_text
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import GroupKFold, GridSearchCV, cross_val_predict
from sklearn.metrics import r2_score, mean_absolute_error

d = pd.read_csv('page_runs_clean.csv')
feat = ['request_count','total_network_bytes','document_bytes','script_bytes','stylesheet_bytes',
        'image_bytes','font_bytes','xhr_fetch_bytes','media_bytes','other_bytes','dom_node_count']
X = pd.get_dummies(d[feat + ['network_profile']], columns=['network_profile'])
y = np.log1p(d.page_load_time_ms)          # skewed target -> log
g = d.url
cv = GroupKFold(5)

gs = GridSearchCV(DecisionTreeRegressor(random_state=0),
    {'max_depth':[3,4,5,6,8],'min_samples_leaf':[5,10,20]}, cv=cv, scoring='r2')
gs.fit(X, y, groups=g)
print('best params', gs.best_params_)

for name, m in [('DecisionTree', gs.best_estimator_),
                ('GradientBoosting', GradientBoostingRegressor(random_state=0)),
                ('Baseline(mean)', None)]:
    if m is None:
        pred = np.full(len(y), y.mean())
    else:
        pred = cross_val_predict(m, X, y, cv=cv, groups=g)
    print(f'{name:18s} R2(log)={r2_score(y,pred):.3f}  MAE={mean_absolute_error(np.expm1(y),np.expm1(pred)):.0f} ms')

tree = gs.best_estimator_.fit(X, y)
print(export_text(tree, feature_names=list(X.columns), max_depth=3))
print(pd.Series(tree.feature_importances_, X.columns).sort_values(ascending=False).head(6))
