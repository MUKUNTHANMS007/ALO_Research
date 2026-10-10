import pandas as pd

p = pd.read_csv('page_runs.csv')
n = pd.read_csv('network_requests.csv', low_memory=False)
print('raw runs', len(p))

# failed navigations: load times are not real measurements
p = p[p.navigation_status == 'success']
# page "loaded" with nothing / implausibly fast = broken capture
p = p[(p.request_count > 1) & (p.total_network_bytes > 0) & (p.page_load_time_ms > 100)]
# columns that are constant (CPU all zero), free text, or empty
drop = [c for c in p.columns if p[c].nunique() <= 1] + ['error_message', 'title', 'timestamp_utc']
p = p.drop(columns=set(drop) & set(p.columns))
print('dropped cols', sorted(set(drop)))

# keep only URLs that still have all 9 runs' worth of signal (>=1 run per profile)
ok = p.groupby('url').network_profile.nunique()
p = p[p.url.isin(ok[ok == 3].index)]

# per-request features aggregated to run level
n = n[n.run_id.isin(p.run_id)]
agg = n.groupby('run_id').agg(
    third_party_hosts=('resource_url', lambda s: s.str.extract(r'//([^/]+)')[0].nunique()),
    max_request_ms=('duration_ms', 'max'),
).reset_index()
p = p.merge(agg, on='run_id', how='left')

p.to_csv('page_runs_clean.csv', index=False)
print('clean runs', len(p), 'urls', p.url.nunique())
print(p.network_profile.value_counts())
