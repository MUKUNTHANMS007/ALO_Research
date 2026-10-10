"""ALO tray agent: runs in the background from login, with an On/Off switch in the system tray.

    pythonw agent\\alo_agent.pyw        (no console window)   or   python -m agent.tray

It starts the ALO API + dashboard on http://localhost:8000 if nothing is serving there yet (the API hosts the
telemetry sampler), and controls it over HTTP, so it also works when the API was started separately."""
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000
BASE = f'http://localhost:{PORT}'
RUN_KEY, RUN_NAME = r'Software\Microsoft\Windows\CurrentVersion\Run', 'ALO Agent'


def api(path, body=None, timeout=3):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def port_open():
    try:
        socket.create_connection(('127.0.0.1', PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


def start_server():
    if port_open():
        return
    import uvicorn
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    cfg = uvicorn.Config('api.main:app', port=PORT, log_level='warning')
    threading.Thread(target=uvicorn.Server(cfg).run, daemon=True, name='alo-api').start()


# ---- start with Windows (current user, no admin needed) ----
def autostart_on():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            exe = Path(sys.executable)
            exe = exe.with_name('pythonw.exe') if exe.with_name('pythonw.exe').exists() else exe
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{exe}" "{ROOT / "agent" / "alo_agent.pyw"}"')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


def icon_image(on):
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(2, 132, 199, 255) if on else (148, 163, 184, 255))
    d.polygon([(36, 10), (20, 36), (31, 36), (27, 54), (44, 26), (33, 26)], fill=(255, 255, 255, 255))  # lightning bolt
    return img


def main():
    start_server()
    for _ in range(60):  # wait for the API (loading the model takes a few seconds)
        try:
            api('/api/health')
            break
        except OSError:
            time.sleep(1)

    def enabled():
        try:
            return api('/api/agent/status')['enabled']
        except OSError:
            return False

    def toggle(icon, item):
        try:
            api('/api/agent/toggle', {'enabled': not enabled()})
        except OSError:
            pass
        refresh(icon)

    def refresh(icon):
        on = enabled()
        icon.icon = icon_image(on)
        icon.title = 'ALO is ON - measuring this PC\'s network' if on else 'ALO is OFF'
        icon.update_menu()

    def toggle_autostart(icon, item):
        set_autostart(not autostart_on())

    menu = pystray.Menu(
        pystray.MenuItem('ALO enabled', toggle, checked=lambda i: enabled()),
        pystray.MenuItem('Open dashboard', lambda i, it: webbrowser.open(BASE + '/#agent'), default=True),
        pystray.MenuItem('Start with Windows', toggle_autostart, checked=lambda i: autostart_on()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit', lambda i, it: i.stop()),
    )
    icon = pystray.Icon('alo', icon_image(enabled()), 'ALO', menu)

    def setup(icon):
        icon.visible = True
        refresh(icon)
        while icon.visible:  # keep the icon in sync if the switch is flipped from the dashboard
            time.sleep(5)
            try:
                refresh(icon)
            except Exception:
                pass

    icon.run(setup)


if __name__ == '__main__':
    main()
