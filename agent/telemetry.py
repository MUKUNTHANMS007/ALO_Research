"""ALO OS agent: system-wide network telemetry.

While enabled it samples, every few seconds, what the whole machine is doing on the network (no browser needed):
  * system CPU / RAM, download / upload rate, round-trip time to a public host, and the network profile that implies;
  * which applications hold open internet connections, and how long a new TCP connection to their remote endpoints
    takes (a real per-app latency measurement).
Privacy: only process names and aggregate numbers are stored (collected/os_telemetry.csv, collected/os_apps.csv).
Remote addresses and page URLs are kept in memory for the probe only and never written to disk.
Disabled = nothing is sampled at all. The on/off state persists in agent_state.json."""
import csv
import ipaddress
import json
import socket
import statistics
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / 'collected'
STATE_PATH = ROOT / 'agent_state.json'
PROBES = [('1.1.1.1', 443), ('8.8.8.8', 53), ('9.9.9.9', 443)]
SYS_FIELDS = ['ts', 'profile', 'rtt_ms', 'cpu', 'ram', 'down_kbps', 'up_kbps', 'apps_online', 'connections',
              'top_app', 'top_app_connections', 'median_app_connect_ms']
APP_FIELDS = ['ts', 'app', 'connections', 'connect_ms']


def network_profile(rtt):
    """Map measured RTT to the throttle profile the model was trained on (same rule as api/main.py)."""
    if rtt is None:
        return 'Slow_3G'
    return 'No_Throttle' if rtt < 30 else 'Fast_4G' if rtt < 200 else 'Slow_3G'


def tcp_connect_ms(host, port, timeout=1.5):
    t = time.perf_counter()
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return (time.perf_counter() - t) * 1000
    except OSError:
        return None


def probe_rtt():
    vals = [v for v in (tcp_connect_ms(h, p, 2) for h, p in PROBES[:2]) if v is not None]
    return statistics.median(vals) if vals else None


class Telemetry:
    def __init__(self, interval=5, probe_every=3):
        self.interval, self.probe_every = interval, probe_every
        self.enabled = self._load_enabled()
        self.history = deque(maxlen=720)          # about 1 hour at 5 s
        self.apps = {}                            # name -> {'conns', 'connect': deque, 'seen'}
        self.samples = 0
        self.started = time.time()
        self.error = None
        self._lock = threading.Lock()
        self._names = {}
        self._tick = 0
        self._io = psutil.net_io_counters()
        self._io_t = time.time()
        self._thread = None
        self._pool = ThreadPoolExecutor(max_workers=8)

    # ---- state ----
    @staticmethod
    def _load_enabled():
        try:
            return bool(json.load(open(STATE_PATH)).get('enabled', True))
        except (OSError, ValueError):
            return True

    def set_enabled(self, value):
        self.enabled = bool(value)
        try:
            json.dump({'enabled': self.enabled}, open(STATE_PATH, 'w'))
        except OSError:
            pass
        if self.enabled:  # reset the rate baseline so the first sample after turning on is not an average over the pause
            self._io, self._io_t = psutil.net_io_counters(), time.time()

    def start(self):
        if self._thread is None:
            psutil.cpu_percent(None)
            self._thread = threading.Thread(target=self._loop, daemon=True, name='alo-telemetry')
            self._thread.start()

    # ---- sampling ----
    def _name(self, pid):
        if pid not in self._names:
            try:
                self._names[pid] = psutil.Process(pid).name()
            except psutil.Error:
                self._names[pid] = None
        return self._names[pid]

    def _loop(self):
        while True:
            t0 = time.time()
            if self.enabled:
                try:
                    self._sample()
                    self.error = None
                except Exception as e:  # keep the agent alive whatever the OS throws
                    self.error = f'{type(e).__name__}: {e}'
            time.sleep(max(0.5, self.interval - (time.time() - t0)))

    def _sample(self):
        now = time.time()
        self._tick += 1
        probing = self._tick % self.probe_every == 1
        if len(self._names) > 4000:
            self._names.clear()

        io = psutil.net_io_counters()
        dt = max(0.2, now - self._io_t)
        down, up = (io.bytes_recv - self._io.bytes_recv) * 8 / 1000 / dt, (io.bytes_sent - self._io.bytes_sent) * 8 / 1000 / dt
        self._io, self._io_t = io, now

        per = defaultdict(lambda: {'conns': 0, 'remotes': set()})
        total = 0
        for k in psutil.net_connections(kind='tcp'):
            if k.status != 'ESTABLISHED' or not k.raddr or not k.pid:
                continue
            try:
                if not ipaddress.ip_address(k.raddr.ip).is_global:
                    continue
            except ValueError:
                continue
            name = self._name(k.pid)
            if not name:
                continue
            per[name]['conns'] += 1
            per[name]['remotes'].add((k.raddr.ip, k.raddr.port))
            total += 1

        rtt = probe_rtt() if probing else (self.history[-1]['rtt_ms'] if self.history else probe_rtt())

        if probing:  # measure real connect time to up to 2 endpoints per app (max 12 probes)
            jobs = []
            for name, d in sorted(per.items(), key=lambda x: -x[1]['conns']):
                for r in list(d['remotes'])[:2]:
                    if len(jobs) < 12:
                        jobs.append((name, r, self._pool.submit(tcp_connect_ms, r[0], r[1])))
            results = defaultdict(list)
            for name, _, fut in jobs:
                v = fut.result()
                if v is not None:
                    results[name].append(v)
        else:
            results = {}

        with self._lock:
            for name, d in per.items():
                a = self.apps.setdefault(name, {'conns': 0, 'connect': deque(maxlen=30), 'seen': 0})
                a['conns'], a['seen'] = d['conns'], now
                a['connect'].extend(results.get(name, []))
            for name in [n for n, a in self.apps.items() if now - a['seen'] > 120]:
                del self.apps[name]
            current = {n: a for n, a in self.apps.items() if now - a['seen'] < self.interval * 2 + 1}
            top = max(per.items(), key=lambda x: x[1]['conns'], default=(None, {'conns': 0}))
            med = [statistics.median(a['connect']) for a in current.values() if a['connect']]
            row = {'ts': round(now, 1), 'profile': network_profile(rtt), 'rtt_ms': None if rtt is None else round(rtt, 1),
                   'cpu': psutil.cpu_percent(None), 'ram': psutil.virtual_memory().percent,
                   'down_kbps': round(down, 1), 'up_kbps': round(up, 1), 'apps_online': len(per), 'connections': total,
                   'top_app': top[0], 'top_app_connections': top[1]['conns'],
                   'median_app_connect_ms': round(statistics.median(med), 1) if med else None}
            self.history.append(row)
            self.samples += 1
        self._write(row, per, results, probing)

    def _write(self, row, per, results, probing):
        try:
            OUT_DIR.mkdir(exist_ok=True)
            for path, fields, rows in (
                    (OUT_DIR / 'os_telemetry.csv', SYS_FIELDS, [row]),
                    (OUT_DIR / 'os_apps.csv', APP_FIELDS,
                     [{'ts': row['ts'], 'app': n, 'connections': d['conns'],
                       'connect_ms': round(statistics.median(results[n]), 1) if results.get(n) else ''}
                      for n, d in per.items()] if probing else [])):
                if not rows:
                    continue
                new = not path.exists()
                with open(path, 'a', newline='', encoding='utf-8') as f:
                    w = csv.DictWriter(f, fields)
                    if new:
                        w.writeheader()
                    w.writerows(rows)
        except OSError:
            pass

    # ---- read side ----
    def status(self):
        with self._lock:
            now = time.time()
            last = self.history[-1] if self.history else None
            apps = sorted(({'app': n, 'connections': a['conns'],
                            'connect_ms': round(statistics.median(a['connect']), 1) if a['connect'] else None,
                            'probes': len(a['connect'])}
                           for n, a in self.apps.items() if now - a['seen'] < self.interval * 2 + 1),
                          key=lambda x: -x['connections'])
            return {'enabled': self.enabled, 'samples': self.samples, 'uptime_s': round(now - self.started),
                    'interval_s': self.interval, 'error': self.error, 'last': last, 'apps': apps,
                    'files': {'system': 'collected/os_telemetry.csv', 'apps': 'collected/os_apps.csv'}}

    def series(self, n=120):
        with self._lock:
            return list(self.history)[-n:]
