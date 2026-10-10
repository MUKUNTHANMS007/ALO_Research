"""ALO collector: URL x network x cpu x cache x variant x repeat -> collected/runs.csv
Usage: python collect.py [--urls urls.txt] [--repeats 3] [--limit N] [--quick] [--retries 2]

Reliability:
  * Resumable: re-run and it skips finished combinations.
  * Offline-aware: before each run it checks the internet; if the connection is down it WAITS and then retries
    the same run. Offline failures are never written as data.
  * Retries other failures (timeouts, crashes) up to --retries more times; runs that still fail go to
    collected/failed.csv (not into the training file) and are not retried on resume.
  * Breadth-first order: every URL gets its baseline runs before any URL gets a second pass, so stopping early
    still leaves data spread across many websites.
  * Records device state (CPU, memory, battery, latency) just before each run, for use as model inputs."""
import argparse, csv, itertools, os, socket, time, uuid, datetime as dt
from urllib.parse import urlparse
import psutil
from playwright.sync_api import sync_playwright

NETWORK = {  # CDP: latency ms, bytes/s
    'No_Throttle': None,
    'Fast_4G': dict(latency=40, down=9 * 1024 * 1024 / 8, up=1.5 * 1024 * 1024 / 8),
    'Slow_3G': dict(latency=400, down=400 * 1024 / 8, up=400 * 1024 / 8),
}
CPU = {'cpu_1x': 1, 'cpu_4x': 4}
CACHE = ['cold', 'warm']
# Variants = simulated optimisations by request blocking. They give an UPPER BOUND on what
# the action could save (the resource is removed, not shrunk); treat them that way in analysis.
VARIANTS = {
    'baseline': lambda r, page: False,
    'no_images': lambda r, page: r.resource_type == 'image',
    'no_third_party_scripts': lambda r, page: r.resource_type == 'script' and urlparse(r.url).netloc != urlparse(page).netloc,
    'no_fonts': lambda r, page: r.resource_type == 'font',
    'no_media': lambda r, page: r.resource_type == 'media',
}
INIT_JS = """
window.__m={fcp:null,lcp:null,cls:0,tbt:0};
new PerformanceObserver(l=>{for(const e of l.getEntries())if(e.name==='first-contentful-paint')__m.fcp=e.startTime}).observe({type:'paint',buffered:true});
new PerformanceObserver(l=>{for(const e of l.getEntries())__m.lcp=e.startTime}).observe({type:'largest-contentful-paint',buffered:true});
new PerformanceObserver(l=>{for(const e of l.getEntries())if(!e.hadRecentInput)__m.cls+=e.value}).observe({type:'layout-shift',buffered:true});
new PerformanceObserver(l=>{for(const e of l.getEntries())if(e.duration>50)__m.tbt+=e.duration-50}).observe({type:'longtask',buffered:true});
"""
FIELDS = ['run_id', 'timestamp_utc', 'url', 'network_profile', 'cpu_profile', 'cache_state', 'variant', 'repeat_number',
          'navigation_status', 'error_message', 'ttfb_ms', 'fcp_ms', 'lcp_ms', 'cls', 'tbt_ms', 'dom_content_loaded_ms',
          'page_load_time_ms', 'request_count', 'failed_request_count', 'blocked_request_count', 'total_network_bytes',
          'document_bytes', 'script_bytes', 'stylesheet_bytes', 'image_bytes', 'font_bytes', 'xhr_fetch_bytes',
          'media_bytes', 'other_bytes', 'third_party_request_count', 'js_heap_used_mb', 'script_duration_ms',
          'layout_duration_ms', 'recalc_style_duration_ms', 'task_duration_ms', 'cpu_peak_percent', 'cpu_avg_percent',
          'rss_peak_mb', 'rss_avg_mb', 'dom_node_count', 'viewport', 'device_cores', 'device_memory_gb',
          'sys_cpu_before', 'sys_ram_before', 'sys_battery', 'sys_plugged', 'sys_rtt_before_ms', 'attempts']
BUCKET = {'Document': 'document', 'Script': 'script', 'Stylesheet': 'stylesheet', 'Image': 'image', 'Font': 'font',
          'XHR': 'xhr_fetch', 'Fetch': 'xhr_fetch', 'Media': 'media'}


_PROCS = {}


def tree_procs(pid):
    """Cached Process objects: cpu_percent() needs the same object across calls to be non-zero."""
    try:
        live = [psutil.Process(pid)] + psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return []
    out = []
    for p in live:
        if p.pid not in _PROCS:
            _PROCS[p.pid] = p
            p.cpu_percent(None)
        out.append(_PROCS[p.pid])
    return out


def sample(pid, cpu, rss):
    tot_c = tot_r = 0.0
    for p in tree_procs(pid):
        try:
            tot_c += p.cpu_percent(None)
            tot_r += p.memory_info().rss
        except psutil.Error:
            pass
    cpu.append(tot_c)
    rss.append(tot_r / 2 ** 20)


def run_once(browser, pid, url, net, cpu_n, cache, variant, timeout_ms):
    ctx = browser.new_context(viewport={'width': 1366, 'height': 768}, service_workers='block')
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    cdp.send('Network.enable')
    cdp.send('Performance.enable')
    cdp.send('Network.setCacheDisabled', {'cacheDisabled': cache == 'cold'})
    if NETWORK[net]:
        n = NETWORK[net]
        cdp.send('Network.emulateNetworkConditions', dict(offline=False, latency=n['latency'],
                                                          downloadThroughput=n['down'], uploadThroughput=n['up']))
    if CPU[cpu_n] > 1:
        cdp.send('Emulation.setCPUThrottlingRate', {'rate': CPU[cpu_n]})
    page.add_init_script(INIT_JS)

    reqs, blocked = {}, [0]
    cdp.on('Network.requestWillBeSent', lambda e: reqs.__setitem__(
        e['requestId'], {'type': e.get('type', 'Other'), 'bytes': 0, 'failed': False, 'url': e['request']['url']}))
    cdp.on('Network.loadingFinished', lambda e: reqs.get(e['requestId'], {}).__setitem__('bytes', e.get('encodedDataLength', 0)))
    cdp.on('Network.loadingFailed', lambda e: reqs.get(e['requestId'], {}).__setitem__('failed', True))

    rule = VARIANTS[variant]

    def route(r):
        if variant != 'baseline' and rule(r.request, url):
            blocked[0] += 1
            r.abort()
        else:
            r.continue_()
    page.route('**/*', route)

    status, err = 'success', ''
    if cache == 'warm':  # prime the cache once, then measure the second load
        try:
            page.goto(url, wait_until='load', timeout=timeout_ms)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        reqs.clear()
        blocked[0] = 0
    cpu, rss = [], []
    for p in tree_procs(pid):
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    t0 = time.time()
    try:
        page.goto(url, wait_until='load', timeout=timeout_ms)
        t1 = time.time()
        while time.time() - t1 < 4:  # settle: gather LCP/CLS/TBT and CPU
            sample(pid, cpu, rss)
            page.wait_for_timeout(250)
    except Exception as e:
        status, err = 'failed', str(e).splitlines()[0][:200]
    row = dict(navigation_status=status, error_message=err)
    try:
        nav = page.evaluate("(()=>{const n=performance.getEntriesByType('navigation')[0];"
                            "return n?{ttfb:n.responseStart,dcl:n.domContentLoadedEventEnd,load:n.loadEventEnd}:null})()")
        m = page.evaluate('window.__m')
        pm = {x['name']: x['value'] for x in cdp.send('Performance.getMetrics')['metrics']}
        row.update(ttfb_ms=nav['ttfb'], dom_content_loaded_ms=nav['dcl'], page_load_time_ms=nav['load'],
                   fcp_ms=m['fcp'], lcp_ms=m['lcp'], cls=m['cls'], tbt_ms=m['tbt'],
                   js_heap_used_mb=pm.get('JSHeapUsedSize', 0) / 2 ** 20,
                   script_duration_ms=pm.get('ScriptDuration', 0) * 1000,
                   layout_duration_ms=pm.get('LayoutDuration', 0) * 1000,
                   recalc_style_duration_ms=pm.get('RecalcStyleDuration', 0) * 1000,
                   task_duration_ms=pm.get('TaskDuration', 0) * 1000, dom_node_count=pm.get('Nodes', 0))
        env = page.evaluate('({c:navigator.hardwareConcurrency,m:navigator.deviceMemory})')
        row.update(device_cores=env['c'], device_memory_gb=env['m'])
    except Exception as e:
        if status == 'success':
            row.update(navigation_status='failed', error_message='metrics: ' + str(e)[:150])
    host = urlparse(url).netloc
    b = {k: 0 for k in ['document', 'script', 'stylesheet', 'image', 'font', 'xhr_fetch', 'media', 'other']}
    for r in reqs.values():
        b[BUCKET.get(r['type'], 'other')] += r['bytes']
    row.update(request_count=len(reqs), failed_request_count=sum(r['failed'] for r in reqs.values()),
               blocked_request_count=blocked[0], total_network_bytes=sum(r['bytes'] for r in reqs.values()),
               third_party_request_count=sum(urlparse(r['url']).netloc != host for r in reqs.values()),
               **{k + '_bytes': v for k, v in b.items()},
               cpu_peak_percent=max(cpu, default=0), cpu_avg_percent=sum(cpu) / len(cpu) if cpu else 0,
               rss_peak_mb=max(rss, default=0), rss_avg_mb=sum(rss) / len(rss) if rss else 0, viewport='1366x768')
    ctx.close()
    return row


OFFLINE_ERRORS = ('ERR_INTERNET_DISCONNECTED', 'ERR_NETWORK_CHANGED', 'ERR_NETWORK_ACCESS_DENIED',
                  'ERR_PROXY_CONNECTION_FAILED', 'ERR_ADDRESS_UNREACHABLE')
PROBES = [('1.1.1.1', 443), ('8.8.8.8', 53), ('9.9.9.9', 443)]


def probe_rtt():
    """Milliseconds to open a TCP connection to a public host, or None if every probe fails."""
    for host, port in PROBES:
        t = time.perf_counter()
        try:
            socket.create_connection((host, port), timeout=3).close()
            return (time.perf_counter() - t) * 1000
        except OSError:
            continue
    return None


def wait_online(max_wait_s=None):
    """Block until the internet is reachable. Returns seconds waited."""
    start, announced, delay = time.time(), False, 5
    while probe_rtt() is None:
        if not announced:
            print('  ! connection lost, pausing until it returns...', flush=True)
            announced = True
        if max_wait_s and time.time() - start > max_wait_s:
            raise SystemExit('offline too long, stopping (re-run to resume)')
        time.sleep(delay)
        delay = min(60, delay * 1.5)
    if announced:
        print(f'  ok connection back after {time.time() - start:.0f}s, resuming', flush=True)
    return time.time() - start


def device_state():
    rtt = probe_rtt()
    bat = psutil.sensors_battery()
    return dict(sys_cpu_before=psutil.cpu_percent(interval=0.5), sys_ram_before=psutil.virtual_memory().percent,
                sys_battery=bat.percent if bat else '', sys_plugged=bat.power_plugged if bat else '',
                sys_rtt_before_ms=round(rtt, 1) if rtt is not None else '')


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def key_of(r):
    return (r['url'], r['network_profile'], r['cpu_profile'], r['cache_state'], r['variant'], str(r['repeat_number']))


def is_offline_error(msg):
    return any(m in (msg or '') for m in OFFLINE_ERRORS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--urls', default='urls.txt')
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--timeout', type=int, default=90)
    ap.add_argument('--retries', type=int, default=2, help='extra attempts for non-network failures')
    ap.add_argument('--quick', action='store_true', help='smoke test: baseline+no_images, cpu_1x, cold only')
    ap.add_argument('--out', default='collected/runs.csv')
    ap.add_argument('--variants', help='comma list, e.g. baseline,no_fonts (default: all)')
    ap.add_argument('--cpus', help='comma list of cpu_1x,cpu_4x (default: both)')
    ap.add_argument('--caches', help='comma list of cold,warm (default: both)')
    a = ap.parse_args()
    urls = [u.strip() for u in open(a.urls) if u.strip()][:a.limit]
    variants = ['baseline', 'no_images'] if a.quick else list(VARIANTS)
    cpus = ['cpu_1x'] if a.quick else list(CPU)
    caches = ['cold'] if a.quick else CACHE
    if a.variants:
        variants = a.variants.split(',')
    if a.cpus:
        cpus = a.cpus.split(',')
    if a.caches:
        caches = a.caches.split(',')
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    failed_path = os.path.join(os.path.dirname(a.out) or '.', 'failed.csv')

    # Existing file: keep successes, move failures out. Offline failures are discarded (they will be re-run).
    old = read_rows(a.out)
    good = [r for r in old if r['navigation_status'] == 'success']
    moved = [r for r in old if r['navigation_status'] != 'success']
    if moved:
        keep_failed = [r for r in moved if not is_offline_error(r.get('error_message'))]
        with open(failed_path, 'a', newline='', encoding='utf-8') as ff:
            fw = csv.DictWriter(ff, FIELDS, extrasaction='ignore')
            if ff.tell() == 0:
                fw.writeheader()
            fw.writerows(keep_failed)
        with open(a.out, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, FIELDS, extrasaction='ignore')
            w.writeheader()
            w.writerows(good)
        print(f'cleaned {a.out}: kept {len(good)} successes, moved {len(keep_failed)} real failures to {failed_path}, '
              f'dropped {len(moved) - len(keep_failed)} offline failures (will re-run)')
    done = {key_of(r) for r in good} | {key_of(r) for r in read_rows(failed_path)
                                        if not is_offline_error(r.get('error_message'))}
    new = not os.path.exists(a.out)

    # breadth-first: repeat > variant > cpu > cache > network > url
    combos = [(u, net, c, ca, v, rep) for rep, v, c, ca, net, u in
              itertools.product(range(1, a.repeats + 1), variants, cpus, caches, NETWORK, urls)]
    todo = [c for c in combos if (c[0], c[1], c[2], c[3], c[4], str(c[5])) not in done]
    print(f'{len(combos)} runs planned, {len(combos) - len(todo)} already done, {len(todo)} to go')
    if not todo:
        return

    t_start, finished, n_fail, n_wait = time.time(), 0, 0, 0.0
    with sync_playwright() as pw, open(a.out, 'a', newline='', encoding='utf-8') as f, \
            open(failed_path, 'a', newline='', encoding='utf-8') as ff:
        w = csv.DictWriter(f, FIELDS, extrasaction='ignore')
        fw = csv.DictWriter(ff, FIELDS, extrasaction='ignore')
        if new:
            w.writeheader()
        if ff.tell() == 0:
            fw.writeheader()
        browser = pid = None

        def launch():
            nonlocal browser, pid
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            _PROCS.clear()
            browser = pw.chromium.launch(headless=True)
            pid = browser.process.pid if hasattr(browser, 'process') else None
        launch()

        for i, (u, net, c, ca, v, rep) in enumerate(todo, 1):
            t = time.time()
            attempts, row = 0, None
            while True:
                n_wait += wait_online()
                dev = device_state()
                attempts += 1
                try:
                    row = run_once(browser, pid, u, net, c, ca, v, a.timeout * 1000)
                except Exception as e:  # browser crash etc.
                    row = dict(navigation_status='failed', error_message='crash: ' + str(e).splitlines()[0][:150])
                    launch()
                row.update(dev)
                if row['navigation_status'] == 'success':
                    break
                if is_offline_error(row.get('error_message')) or probe_rtt() is None:
                    attempts -= 1      # not the page's fault: wait for the connection and try again
                    continue
                if attempts > a.retries:
                    break
            row.update(run_id=str(uuid.uuid4()), timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat(), url=u,
                       network_profile=net, cpu_profile=c, cache_state=ca, variant=v, repeat_number=rep, attempts=attempts)
            ok = row['navigation_status'] == 'success'
            (w if ok else fw).writerow(row)
            (f if ok else ff).flush()
            finished += 1
            n_fail += not ok
            eta_min = (time.time() - t_start) / finished * (len(todo) - finished) / 60
            print(f'[{i}/{len(todo)}] {u} {net} {c} {ca} {v} #{rep} -> {row["navigation_status"]} '
                  f'{time.time() - t:.0f}s  (failed so far {n_fail}, ETA {eta_min:.0f} min)', flush=True)
            if i % 150 == 0:
                launch()           # keep the browser fresh on long runs
        browser.close()
    print(f'done: {finished - n_fail} saved, {n_fail} failed (see {failed_path}), waited {n_wait / 60:.1f} min offline')


if __name__ == '__main__':
    main()
