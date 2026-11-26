"""The browser workbench every emulated radio shares (sim/uv21, sim/rt950, and SoapyFang's on
both): one thread owns the emulator and steps it all the time, the page polls the screen and posts
keys. After flux_radio's sim/k6ide.py (a K6 workbench, MIT). Standard library only (no Flask).
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: F401  (for the radios)


class Workbench:
    """one thread owns the board and steps it all the time; the page polls the screen and posts
    keys. A radio's workbench gives _boot() (set self.board, self.serial, self.wall0, self.virt0),
    KEYS, and the board: step(n) (returns new serial text, or None), press/tap/holding, seconds,
    tuned_hz(), band, reset_requested and screen_png(scale)."""
    KEYS = ()
    SCALE = 3                               # screen.png's pixels per LCD pixel

    def __init__(self, step=400_000):
        self.lock = threading.RLock()
        self.step_n = step
        self.running = True
        self.error = None
        self.serial = ""
        self.png = b""
        self.png_at = 0.0
        self.queue = []                     # (key, hold seconds) waiting for the board
        self._boot()
        threading.Thread(target=self._run, name="workbench", daemon=True).start()

    def _boot(self):
        raise NotImplementedError

    def _run(self):
        while True:
            if not self.running:
                time.sleep(0.05)
                continue
            try:
                with self.lock:
                    b = self.board
                    if self.queue and b.holding is None:
                        key, hold = self.queue.pop(0)
                        if hold is None:
                            b.press(key, True)
                            b.holding = (key, 1 << 62)      # until the page lets go
                        else:
                            b.tap(key, hold)
                    out = b.step(self.step_n)
                    if out:
                        self.serial = (self.serial + out)[-8000:]
                    if b.reset_requested:
                        self.serial += "\n[the image asked for a reset: rebooting]\n"
                        self._boot()
            except Exception as e:          # keep the page up and say what broke
                self.error = repr(e)
                self.running = False
            time.sleep(0.001)               # (let the page's requests have the lock)

    def key(self, key, action):
        if key not in self.KEYS:
            raise ValueError(key)
        with self.lock:
            if action == "tap":
                self.queue.append((key, 0.15))
            elif action == "long":
                self.queue.append((key, 1.3))           # over the firmware's 1 s long press
            elif action == "down":
                self.queue.append((key, None))
            elif action == "up":
                b = self.board
                if b.holding and b.holding[0] == key:
                    b.press(key, False)
                    b.holding = None
                else:
                    self.queue = [q for q in self.queue if q[0] != key]

    def screen(self):
        with self.lock:
            if time.time() - self.png_at > 0.15:
                self.png = self.board.screen_png(self.SCALE)
                self.png_at = time.time()
            return self.png

    def state(self):
        with self.lock:
            b = self.board
            wall = max(1e-6, time.time() - self.wall0)
            return {
                "seconds": round(b.seconds, 2),
                "speed": round((b.seconds - self.virt0) / wall, 3),
                "tuned_hz": b.tuned_hz(),
                "running": self.running,
                "error": self.error,
                "serial": self.serial[-3000:],
                "holding": b.holding[0] if b.holding else None,
                "stations": [{"i": i, "name": s.name, "hz": s.hz, "dbm": s.dbm, "kind": s.kind,
                              "enabled": s.enabled, "keyed": s.keyed(b.seconds)}
                             for i, s in enumerate(b.band)],
            }

    def station(self, i, enabled=None, dbm=None, hz=None):
        with self.lock:
            s = self.board.band[i]
            if enabled is not None:
                s.enabled = bool(enabled)
            if dbm is not None:
                s.dbm = float(dbm)
            if hz is not None:
                s.hz = int(hz)

    def reboot(self):
        with self.lock:
            band = self.board.band
            self._boot()
            self.board.band = band
            self.error, self.running = None, True


def handler(wb: Workbench, page):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body, ctype="application/json", code=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self.send(open(page, "rb").read(), "text/html; charset=utf-8")
            elif path == "/api/screen.png":
                self.send(wb.screen(), "image/png")
            elif path == "/api/state":
                self.send(wb.state())
            else:
                self.send({"error": "not found"}, code=404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                path = self.path.split("?")[0]
                if path == "/api/key":
                    wb.key(req["key"], req.get("action", "tap"))
                elif path == "/api/station":
                    wb.station(int(req["i"]), req.get("enabled"), req.get("dbm"), req.get("hz"))
                elif path == "/api/run":
                    wb.running = bool(req.get("running", True))
                elif path == "/api/reboot":
                    wb.reboot()
                else:
                    return self.send({"error": "not found"}, code=404)
                self.send({"ok": True})
            except Exception as e:
                self.send({"error": repr(e)}, code=400)
    return H
