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
import os
import time

from sim import workbench as base
from sim.workbench import ThreadingHTTPServer

from .board import KEYS, UV21Board

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")


class Workbench(base.Workbench):
    KEYS = KEYS

    def _boot(self):
        with self.lock:
            self.board = UV21Board()
            self.serial = self.board.run(until="boot done", limit=300_000_000)
            self.wall0, self.virt0 = time.time(), self.board.seconds


def handler(wb, page=PAGE):
    return base.handler(wb, page)


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
