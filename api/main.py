"""ALO API. Run from the project root:  python -m uvicorn api.main:app --port 8000
Serves the dashboard at / and the JSON API under /api."""
import json
import csv
import socket
import statistics
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import joblib
import psutil
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from train_model import FEATURES, apply_band, to_frame, to_frame_full

ROOT = Path(__file__).resolve().parent.parent
bundle = joblib.load(ROOT / 'model' / 'alo_tree.joblib')   # single tree: readable rules ("how it decided")
MODEL = bundle['model']
try:                                                        # Extra Trees on 11 features: the predictor
    et_bundle = joblib.load(ROOT / 'model' / 'alo_et.joblib')
except OSError:
    et_bundle = None
PREDICTOR = et_bundle or bundle                             # metrics shown to the user belong to the model that predicts
PROFILES = json.load(open(ROOT / 'model' / 'url_profiles.json'))

app = FastAPI(title='ALO API')


# ---------- live network probe (background so /api/system never blocks) ----------
_net = {'rtt_ms': None, 'ts': 0.0}


def _probe_rtt():
    while True:
        samples = []
        for _ in range(3):
            t = time.perf_counter()
            try:
                socket.create_connection(('1.1.1.1', 443), timeout=2).close()
                samples.append((time.perf_counter() - t) * 1000)
            except OSError:
                pass
        _net['rtt_ms'] = statistics.median(samples) if samples else None
        _net['ts'] = time.time()
        time.sleep(3)


threading.Thread(target=_probe_rtt, daemon=True).start()
psutil.cpu_percent(None)  # prime


def network_profile(rtt):
    """Map measured RTT to the throttle profile the model was trained on (coarse; see notes)."""
    if rtt is None:
        return 'Slow_3G'
    return 'No_Throttle' if rtt < 30 else 'Fast_4G' if rtt < 200 else 'Slow_3G'


PROFILE_LABEL = {'No_Throttle': 'Wired / Wi-Fi (fast)', 'Fast_4G': 'Fast 4G-like', 'Slow_3G': 'Slow 3G-like'}


@app.get('/api/health')
def health():
    return {'ok': True, 'model': PREDICTOR['metrics'], 'rules_model': bundle['metrics']}


_io = {'t': time.time(), 'sent': 0, 'recv': 0}
_c = psutil.net_io_counters()
_io.update(sent=_c.bytes_sent, recv=_c.bytes_recv)
psutil.cpu_percent(None, percpu=True)  # prime per-core counters


@app.get('/api/system')
def system():
    bat = psutil.sensors_battery()
    rtt = _net['rtt_ms']
    prof = network_profile(rtt)
    now, c = time.time(), psutil.net_io_counters()
    dt = max(0.2, now - _io['t'])
    up, down = (c.bytes_sent - _io['sent']) / dt, (c.bytes_recv - _io['recv']) / dt
    _io.update(t=now, sent=c.bytes_sent, recv=c.bytes_recv)
    vm = psutil.virtual_memory()
    return {'cpu': psutil.cpu_percent(None), 'cores': psutil.cpu_percent(None, percpu=True),
            'ram': vm.percent, 'ram_used_gb': round(vm.used / 2 ** 30, 1), 'ram_total_gb': round(vm.total / 2 ** 30, 1),
            'rtt': rtt, 'bat': bat.percent if bat else None, 'plugged': bat.power_plugged if bat else None,
            'net_up_kbps': up * 8 / 1000, 'net_down_kbps': down * 8 / 1000,
            'network': PROFILE_LABEL[prof], 'profile': prof}


# ---------- prediction ----------
class PageIn(BaseModel):
    """Page weight observed in the browser (Resource Timing), so the model need not guess it from the URL."""
    bytes: dict[str, int] = {}    # resource type (document, script, ...) -> transferred bytes that the browser disclosed
    unknown: dict[str, int] = {}  # resource type -> number of requests whose size the browser hides (cross-origin)
    requests: int | None = None


class PredictIn(BaseModel):
    url: str
    profile: str | None = None  # override; default = derived from live RTT
    disabled: list[str] = []  # method names switched off in the Optimizations page
    min_saving_ms: float | None = None  # if set: apply every method saving at least this; else top 3
    page: PageIn | None = None  # real page weight from the extension; default = stored profile for the URL


# Effective download rate per profile (bits/s). No_Throttle is an assumed 25 Mbps, not measured.
THROUGHPUT_BPS = {'Slow_3G': 400_000, 'Fast_4G': 9_000_000, 'No_Throttle': 25_000_000}
PARALLEL_DISCOUNT = 0.6  # not all bytes are on the critical path

# Measured effect of removing images (train_savings.py): fraction of load time saved = slope[network] * image byte share.
# Other methods have no measured data yet and keep the byte-throughput estimate above.
try:
    CALIB = json.load(open(ROOT / 'model' / 'savings_calibration.json'))
except (OSError, ValueError):
    CALIB = {'images': {}, 'request_bytes': {}}
MEASURED = {'Lazy-load images': 'image_bytes'}

# Simulated optimisations = fraction of a resource type's bytes removed from the initial load.
ACTIONS = [
    ('Lazy-load images', 'Defer offscreen images', {'image_bytes': 0.5}, 'Lazy loading'),
    ('Defer third-party scripts', 'Load analytics/ads after interaction', {'script_bytes': 0.3}, 'Deferral'),
    ('Compress / defer media', 'Serve smaller video and poster', {'media_bytes': 0.6}, 'Compression'),
    ('Limit web fonts', 'Subset fonts, preload key ones', {'font_bytes': 0.5}, 'Subsetting'),
]


INTERVAL = PREDICTOR['metrics'].get('interval')  # prediction range calibrated on held-out errors (train_model.py)


def _range(ms, profile):
    """[low_s, high_s] that contained the actual load time ~80% of the time on held-out websites, or None."""
    if not INTERVAL:
        return None
    lo, hi = apply_band(INTERVAL['table'], ms, profile)
    return [lo / 1000, hi / 1000]


def _ranges(baseline_s, optimized_s, profile):
    if not INTERVAL:
        return None
    return {'baseline': _range(baseline_s * 1000, profile), 'optimized': _range(optimized_s * 1000, profile),
            'level': INTERVAL['level'], 'coverage': INTERVAL['coverage_crossfit'].get(profile),
            'note': 'Range of the prediction only; it does not include uncertainty in the savings.'}


def _predict_ms(comp, profile):
    import numpy as np
    row = [{**comp, 'network_profile': profile}]
    if et_bundle:
        return float(np.expm1(et_bundle['model'].predict(to_frame_full(row))[0]))
    return float(np.expm1(MODEL.predict(to_frame(row))[0]))


def explain_path(comp, profile):
    """Human-readable list of the decision-tree splits this page+network went through."""
    X = to_frame([{**comp, 'network_profile': profile}])
    t = MODEL.tree_
    cols = bundle['columns']
    steps = []
    for node in MODEL.decision_path(X).indices:
        if t.children_left[node] == t.children_right[node]:
            continue
        f, thr = cols[t.feature[node]], t.threshold[node]
        v = X.iloc[0][f]
        left = v <= thr
        if f.startswith('network_profile_'):
            name = f.replace('network_profile_', '').replace('_', ' ')
            steps.append(f'Network is {"not " if left else ""}{name}')
        else:
            label = f.replace('_', ' ')
            if f.endswith('_bytes'):
                steps.append(f'{label} = {v / 1024:,.0f} KB {"<=" if left else ">"} {thr / 1024:,.0f} KB')
            else:
                steps.append(f'{label} = {v:,.0f} {"<=" if left else ">"} {thr:,.0f}')
    return steps


_TYPE_KEYS = ['document', 'script', 'stylesheet', 'image', 'font', 'xhr_fetch', 'media', 'other']


def composition_from_page(page: PageIn):
    """Page profile from browser-observed bytes. Cross-origin responses without Timing-Allow-Origin report 0 bytes,
    so those requests are priced at the median size of their type (from the training data)."""
    per_req = CALIB.get('request_bytes', {})
    comp = dict(PROFILES['_global'])
    total = 0
    for t in _TYPE_KEYS:
        b = max(0, page.bytes.get(t, 0)) + max(0, page.unknown.get(t, 0)) * per_req.get(t, 0)
        comp[f'{t}_bytes'] = int(b)
        total += int(b)
    comp['total_network_bytes'] = total
    if page.requests:
        comp['request_count'] = page.requests
    return comp


def _apply(comp, cuts):
    c = dict(comp)
    removed = 0
    for k, frac in cuts.items():
        r = int(c[k] * frac)
        c[k] -= r
        removed += r
    c['total_network_bytes'] = max(0, c['total_network_bytes'] - removed)
    return c


@app.post('/api/predict')
def predict(body: PredictIn):
    url = body.url.strip()
    key = url.rstrip('/').lower()
    known = key in PROFILES
    comp = dict(PROFILES.get(key) or PROFILES['_global'])
    from_browser = bool(body.page and sum(body.page.bytes.values()) + sum(body.page.unknown.values()) > 0)
    if from_browser:
        comp = composition_from_page(body.page)
    elif not known:  # also try matching by hostname (www. optional)
        host = urlparse(url if '//' in url else '//' + url).netloc.lower().removeprefix('www.')
        for k in PROFILES:
            if k != '_global' and urlparse(k).netloc.removeprefix('www.') == host:
                comp, known = dict(PROFILES[k]), True
                break
    profile = body.profile or network_profile(_net['rtt_ms'])
    base = _predict_ms(comp, profile)

    # Per-action saving (the tree is a step function and cannot price small byte cuts). Images use the measured
    # effect: baseline x slope[network] x image byte share x fraction of images cut. Other methods use removed bytes /
    # network throughput (an estimate). Total capped at 70% of baseline.
    bw = THROUGHPUT_BPS[profile]
    results = []
    for name, detail, cuts, method in ACTIONS:
        removed = sum(int(comp[k] * f) for k, f in cuts.items())
        cal = CALIB['images'].get(profile, {}) if name in MEASURED else {}
        slope = cal.get('slope')
        if slope is not None:
            share = comp[MEASURED[name]] / max(1, comp['total_network_bytes'])
            saved_sec = base / 1000 * slope * share * cuts[MEASURED[name]]
        else:
            saved_sec = removed * 8 / bw * PARALLEL_DISCOUNT
        results.append({'name': name, 'detail': detail, 'method': method, 'removedBytes': removed,
                        'savedSec': saved_sec,
                        # 'measured-weak': measured, but the 90% CI of the effect includes zero on this network
                        'basis': 'estimate' if slope is None else 'measured' if cal.get('significant', True) else 'measured-weak'})
    results.sort(key=lambda a: -a['savedSec'])
    total_b = max(1, comp['total_network_bytes'])
    live = [a for a in results if a['name'] not in body.disabled]
    if body.min_saving_ms is None:
        chosen = {a['name'] for a in live[:3]}
        rule = 'top 3'
    else:
        chosen = {a['name'] for a in live if a['savedSec'] * 1000 >= body.min_saving_ms}
        rule = f'saves at least {body.min_saving_ms:g} ms'
    for a in results:
        a['applied'] = a['name'] in chosen
        a['disabled'] = a['name'] in body.disabled
        a['savedMs'] = a['savedSec'] * 1000
        main_key = next(k for n, _, c, _m in ACTIONS if n == a['name'] for k in c)
        share = comp[main_key] / total_b
        what = main_key.replace('_bytes', '')
        how = ('based on runs where images were blocked (upper bound scaled to this cut)' if a['basis'] == 'measured'
               else 'measured by blocking images, but on this network the effect is not distinguishable from zero in '
                    'the data, so treat the saving as unproven' if a['basis'] == 'measured-weak'
               else f"estimated from bytes at {bw / 1e6:.1f} Mbps")
        a['reason'] = (f"{what.capitalize()} is {comp[main_key] / 1024:,.0f} KB ({share:.0%} of the page). On a "
                       f"{PROFILE_LABEL[profile].lower()} link, {a['method'].lower()} removes about "
                       f"{a['removedBytes'] / 1024:,.0f} KB from the first load, saving ~{a['savedMs']:,.0f} ms ({how}). "
                       + ('Turned off in the Optimizations page, so it is not applied.' if a['disabled'] else
                          f'Meets the rule ({rule}), so it is applied.' if a['applied'] else
                          f'Does not meet the rule ({rule}), so it is not applied.'))
    saved_sec = min(sum(a['savedSec'] for a in results if a['applied']), 0.7 * base / 1000)
    optimized = base - saved_sec * 1000
    warning = None
    if comp['total_network_bytes'] < 250_000:
        warning = 'Stored page data for this URL looks incomplete (very few bytes); treat the prediction with care.'

    split = {'Script': 'script_bytes', 'Image': 'image_bytes', 'Font': 'font_bytes', 'Stylesheet': 'stylesheet_bytes',
             'Document': 'document_bytes', 'Media': 'media_bytes', 'Other': 'other_bytes'}
    return {'url': url, 'baseline': base / 1000, 'optimized': optimized / 1000, 'saved': (base - optimized) / 1000,
            'profile': profile, 'range': _ranges(base / 1000, optimized / 1000, profile),
            'actions': results, 'warning': warning, 'path': explain_path(comp, profile), 'network': PROFILE_LABEL[profile], 'actual': None,
            'bytes': [{'type': t, 'bytes': comp[k]} for t, k in split.items()],
            'source': 'browser' if from_browser else 'dataset' if known else 'global-median',
            'path_note': 'Simplified view: a single decision tree on page size and network. The prediction itself comes '
                         'from an average of 300 trees that also use script bytes, image bytes and the request count.'
            if et_bundle else None,
            'model': {'r2_log': PREDICTOR['metrics']['r2_log'], 'mae_ms': PREDICTOR['metrics']['mae_ms']}}


METHOD_INFO = {
    'Lazy loading': ('Images below the fold load only when the user scrolls near them.',
                     'Images can appear late while scrolling; above-the-fold images must not be lazy.'),
    'Deferral': ('Third-party scripts (ads, analytics) load after the page is interactive.',
                 'Features that depend on the script start later; can break pages that need it early.'),
    'Compression': ('Large video/media is replaced by a poster or smaller encoding on first load.',
                    'Lower media quality until the user opts in; some CPU cost to decode.'),
    'Subsetting': ('Web fonts are trimmed to the characters used, and only key fonts are preloaded.',
                   'A brief flash of fallback text; missing glyphs if the subset is too small.'),
}


@app.get('/api/methods')
def methods():
    out = []
    for name, detail, cuts, method in ACTIONS:
        what, risk = METHOD_INFO[method]
        (k, f), = cuts.items()
        out.append({'name': name, 'method': method, 'detail': detail, 'what': what, 'risk': risk,
                    'target': k.replace('_bytes', ''), 'cutFraction': f})
    return {'methods': out, 'throughputMbps': {k: v / 1e6 for k, v in THROUGHPUT_BPS.items()},
            'parallelDiscount': PARALLEL_DISCOUNT,
            'formula': 'images: saved = predicted load x measured slope[network] x image byte share x fraction cut; '
                       'other methods: saved ms = removed KB x 8 / throughput x parallel discount (estimate, no measured data)',
            'imageSlope': {k: {'slope': v['slope'], 'ci90': v['slope_ci90'], 'significant': v.get('significant')}
                           for k, v in CALIB['images'].items()}}


@app.get('/api/model')
def model_info():
    import csv
    from sklearn.tree import export_text
    cols = bundle['columns']
    pcols = PREDICTOR['columns']  # importance is reported for the model that predicts
    imp = PREDICTOR['metrics'].get('importance')  # permutation importance on held-out websites (train_model.py)
    if imp is None:  # older model files: impurity importance, which over-rates features with many distinct values
        imp = [{'feature': f, 'value': float(v)} for f, v in zip(pcols, PREDICTOR['model'].feature_importances_)]
    imp = sorted(imp, key=lambda r: -r['value'])[:8]
    comp = PREDICTOR['metrics'].get('comparison', [])
    p = ROOT / 'reports' / 'results.json'
    if not comp and p.exists():  # old 71-site study; only used when the current comparison is missing
        res = json.load(open(p, encoding='utf-8'))['comparison']
        comp = [{'model': k, 'mae_ms': v['mae'][0], 'r2': v['r2'][0]} for k, v in res.items()]
    return {'metrics': PREDICTOR['metrics'], 'comparison': comp,
            'importance': [{'feature': r['feature'].replace('network_profile_', 'network: ').replace('_', ' '),
                            'value': float(r['value'])} for r in imp if r['value'] > 0],
            'importance_kind': 'drop in accuracy (R2) on unseen websites when the input is shuffled'
            if PREDICTOR['metrics'].get('importance') else 'impurity importance',
            'interval': INTERVAL,
            'rules': export_text(MODEL, feature_names=cols, max_depth=4),
            'features': [c for c in pcols if not c.startswith('network_profile_')] + ['network profile']}


# ---------- OS agent: system-wide telemetry (see agent/telemetry.py) ----------
from agent.telemetry import Telemetry  # noqa: E402

TELE = Telemetry()
TELE.start()


class ToggleIn(BaseModel):
    enabled: bool


@app.get('/api/agent/status')
def agent_status():
    s = TELE.status()
    last = s['last']
    if last:  # what ALO would expect for a typical page on the connection the OS is seeing right now
        p = predict(PredictIn(url='https://typical-page.invalid', profile=last['profile'], min_saving_ms=0))
        s['recommendation'] = {'profile': PROFILE_LABEL[last['profile']], 'expected_load_s': p['baseline'],
                               'with_alo_s': p['optimized'], 'methods': [a['name'] for a in p['actions'] if a['applied']]}
    return s


@app.get('/api/agent/series')
def agent_series(n: int = 120):
    return {'rows': TELE.series(min(max(n, 1), 720))}


@app.post('/api/agent/toggle')
def agent_toggle(body: ToggleIn):
    TELE.set_enabled(body.enabled)
    return {'enabled': TELE.enabled}


# ---------- live runs reported by the browser extension ----------
LIVE_PATH = ROOT / 'collected' / 'live_runs.jsonl'
LIVE: list[dict] = []
_live_seq = [0]


class ReportIn(BaseModel):
    url: str
    load_ms: float
    stats: dict = {}
    disabled: list[str] = []  # methods the extension did not apply on this page
    page: PageIn | None = None  # bytes the browser actually transferred (already reduced by the applied methods)


@app.post('/api/report')
def report(body: ReportIn):
    """A page finished loading in the user's browser with ALO applied: store predicted vs measured."""
    p = predict(PredictIn(url=body.url, disabled=body.disabled, min_saving_ms=0, page=body.page))
    if p['source'] == 'browser':
        # The observed bytes are those of the optimized load, so the model's prediction on them is the "optimized"
        # figure (directly comparable to the measured time); baseline = that plus the applied methods' savings.
        p['optimized'], p['baseline'] = p['baseline'], p['baseline'] + p['saved']
        p['range'] = _ranges(p['baseline'], p['optimized'], p['profile'])
    _live_seq[0] += 1
    run = {**p, 'id': f'live{int(time.time())}-{_live_seq[0]}', 'time': time.strftime('%m/%d/%Y, %I:%M:%S %p'),
           'actual': body.load_ms / 1000, 'live': True, 'applied_stats': body.stats}
    LIVE.append(run)
    del LIVE[:-200]
    try:
        with open(LIVE_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': time.time(), 'url': body.url, 'load_ms': body.load_ms, 'stats': body.stats,
                                'predicted_s': run['optimized'], 'baseline_s': run['baseline'],
                                'network': run['network'], 'source': run['source']}) + '\n')
    except OSError:
        pass
    return run


@app.get('/api/live')
def live(after: str | None = None):
    """Runs reported since the given id (all of them if omitted), oldest first."""
    if after:
        ids = [r['id'] for r in LIVE]
        if after in ids:
            return {'runs': LIVE[ids.index(after) + 1:]}
    return {'runs': LIVE[-20:]}


@app.get('/api/comparison/results')
def comparison_results():
    """Return the latest paired extension comparison CSV for the local viewer."""
    path = ROOT / 'extension_comparison_results.csv'
    if not path.exists():
        return {'exists': False, 'rows': [], 'filename': path.name}
    with path.open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    return {'exists': True, 'rows': rows, 'filename': path.name}


@app.get('/')
def index():
    return FileResponse(ROOT / 'ui' / 'index.html')


app.mount('/', StaticFiles(directory=ROOT / 'ui'), name='ui')
