"""A browser workbench for the UV-21 Pro port: the real image running in the emulated board
(sim/uv21/board.py), its screen, a clickable faceplate and the model band's stations.

    python3 -m sim.uv21                     # http://127.0.0.1:5057/
    python3 -m sim.uv21 --host 0.0.0.0      # from another machine on the LAN (no login: LAN only)

After flux_radio's sim/k6ide.py (a K6 workbench, MIT): one thread owns the emulator and
steps it all the time, the page polls the screen and posts keys. Standard library only (no
Flask), so it runs anywhere the tests run. The SPI flash is the emulator's blank one: no radio's
calibration or settings are read or written. SPDX-License-Identifier: MIT"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .board import KEYS, UV21Board

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")


class Workbench:
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
        threading.Thread(target=self._run, name="uv21-workbench", daemon=True).start()

    def _boot(self):
        with self.lock:
            self.board = UV21Board()
            self.serial = self.board.run(until="boot done", limit=300_000_000)
            self.wall0, self.virt0 = time.time(), self.board.seconds

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
        if key not in KEYS:
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
                self.png = self.board.screen_png(3)
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


def handler(wb: Workbench):
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
                self.send(open(PAGE, "rb").read(), "text/html; charset=utf-8")
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default) or 0.0.0.0 for the LAN")
    ap.add_argument("--port", type=int, default=5057)
    a = ap.parse_args(argv)
    print("building and booting the UV-21 port image ...", flush=True)
    wb = Workbench()
    srv = ThreadingHTTPServer((a.host, a.port), handler(wb))
    print("UV-21 workbench on http://%s:%d/ (Ctrl-C stops it)" % (a.host, a.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
