"""Live demo of the ALO OS agent.  Run:  python demo.py   (add --no-browser to skip opening tabs/dashboard)

1. makes sure the agent/API is running and switched on,
2. opens the dashboard's OS Agent page and a few websites (so there is real traffic from several apps),
3. downloads a 10 MB test file from Cloudflare (a visible throughput spike),
4. prints what the agent measures every 5 s for about a minute,
5. shows the OFF switch: sampling stops, then resumes when turned back on.
Only the numbers the agent measured are printed; nothing is changed on the machine."""
import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = 'http://localhost:8000'
SITES = ['https://www.wikipedia.org', 'https://www.bbc.com', 'https://www.github.com', 'https://news.ycombinator.com']
DOWNLOAD = 'https://speed.cloudflare.com/__down?bytes=10000000'


def api(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def ensure_running():
    try:
        api('/api/health')
        return
    except OSError:
        pass
    print('Starting the ALO agent in the background...')
    pyw = Path(sys.executable).with_name('pythonw.exe')
    subprocess.Popen([str(pyw if pyw.exists() else sys.executable), str(ROOT / 'agent' / 'alo_agent.pyw')], cwd=ROOT)
    for _ in range(90):
        try:
            api('/api/health')
            return
        except OSError:
            time.sleep(1)
    sys.exit('The API did not start; see collected/agent_log.txt')


def download():
    try:
        req = urllib.request.Request(DOWNLOAD, headers={'User-Agent': 'Mozilla/5.0 (ALO demo)'})
        with urllib.request.urlopen(req, timeout=60) as r:
            while r.read(65536):
                pass
    except OSError as e:
        print('  (test download failed:', e, ')')


def line(st):
    s = st['last']
    if not s:
        return '  waiting for the first sample...'
    top = st['apps'][0] if st['apps'] else None
    return (f"  {s['profile']:<11} rtt {s['rtt_ms'] or 0:5.0f} ms | down {s['down_kbps'] / 1000:6.1f} Mbps "
            f"up {s['up_kbps'] / 1000:5.1f} | {s['apps_online']:2d} apps, {s['connections']:3d} conns"
            + (f" | busiest: {top['app']} ({top['connections']})" if top else ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--seconds', type=int, default=60)
    a = ap.parse_args()

    ensure_running()
    api('/api/agent/toggle', {'enabled': True})
    print('ALO agent is ON. Dashboard: ' + BASE + '/#agent')
    if not a.no_browser:
        webbrowser.open(BASE + '/#agent')
        time.sleep(2)
        for u in SITES:
            webbrowser.open_new_tab(u)
            time.sleep(1.5)
    print('Downloading a 10 MB test file to create a visible throughput spike...')
    threading.Thread(target=download, daemon=True).start()

    peak, seen = 0.0, {}
    for _ in range(max(1, a.seconds // 5)):
        time.sleep(5)
        st = api('/api/agent/status')
        print(line(st))
        if st['last']:
            peak = max(peak, st['last']['down_kbps'] / 1000)
        for x in st['apps']:
            seen[x['app']] = max(seen.get(x['app'], 0), x['connections'])

    print('\nApplications seen using the internet (peak connections):')
    for name, n in sorted(seen.items(), key=lambda x: -x[1])[:8]:
        print(f'  {name:<36} {n}')
    print(f'Peak download seen: {peak:.1f} Mbps')

    print('\nSwitching ALO OFF...')
    api('/api/agent/toggle', {'enabled': False})
    n1 = api('/api/agent/status')['samples']
    time.sleep(12)
    n2 = api('/api/agent/status')['samples']
    print(f'  samples while off: {n1} -> {n2} (nothing is measured while it is off)')
    api('/api/agent/toggle', {'enabled': True})
    time.sleep(12)
    print(f"  switched back ON: samples now {api('/api/agent/status')['samples']}")
    rec = api('/api/agent/status').get('recommendation')
    if rec:
        print(f"\nALO expects a typical page on this {rec['profile']} connection to take {rec['expected_load_s']:.1f} s, "
              f"{rec['with_alo_s']:.1f} s with ALO ({', '.join(rec['methods'])}).")


if __name__ == '__main__':
    main()
