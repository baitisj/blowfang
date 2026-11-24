"""The UV-21 workbench (sim/uv21/workbench.py) on SoapyFang: the same page, with the board's RF
chip fed from an I/Q source, a SoapyFang panel (source, RSSI, noise, squelch) and the speaker in
the browser.

    python3 -m sim.soapyfang                                    # the synthetic band
    python3 -m sim.soapyfang --source sigmf:recordings/x.sigmf-meta
    python3 -m sim.soapyfang --source soapy:driver=rtlsdr --gain 30
    python3 -m sim.soapyfang --source soapy:driver=plutosdr,uri=ip:192.168.2.1 --gain 40

Receive only: no source opens a transmit stream. On hamlet the Pluto belongs to the bench runner,
so a Pluto session runs as a runner job, not by hand. Standard library and numpy only.
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import argparse
import os
import time

from sim.uv21 import workbench as uv21wb

from .board import SoapyUV21Board
from .sources import open_source

PANEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.html")


class SoapyWorkbench(uv21wb.Workbench):
    def __init__(self, source, step=400_000):
        self.source = source
        self.board = None
        super().__init__(step=step)

    def _boot(self):
        with self.lock:
            old = self.board
            band = old.band if old is not None else None
            if old is not None:
                old.close(close_source=False)
            self.board = SoapyUV21Board(band=band, source=self.source)
            if band is None and hasattr(self.source, "band"):
                self.source.band = self.board.band          # the page's station edits reach the I/Q
            self.serial = self.board.run(until="boot done", limit=300_000_000)
            self.wall0, self.virt0 = time.time(), self.board.seconds

    def reboot(self):
        with self.lock:
            self._boot()
            self.error, self.running = None, True

    def soapyfang(self):
        with self.lock:
            b, c = self.board, self.board.chip
            tuned = b.tuned_hz()
            return {"source": self.source.describe(), "live": self.source.live,
                    "center_hz": c.src.center_hz, "rate": c.rate, "tuned_hz": tuned,
                    "rssi_dbm": round(c.rssi_dbm(tuned), 1) if tuned else None,
                    "noise": c.noise_raw(), "glitch": c.glitch_raw(),
                    "squelch_open": c.noise_raw() <= 60, "retunes": c.retunes,
                    "fast_bus": b.fast_bus}

    def audio(self, after, gated):
        seq, pcm = self.board.chip.pcm_after(after, gated)
        return seq, int(round(self.board.chip.pcm_rate)), pcm.tobytes()


def handler(wb):
    base = uv21wb.handler(wb)

    class H(base):
        def do_GET(self):
            path, _, query = self.path.partition("?")
            q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
            if path == "/":
                page = open(uv21wb.PAGE, encoding="utf-8").read()
                panel = open(PANEL, encoding="utf-8").read()
                return self.send(page.replace("</body>", panel + "</body>"), "text/html; charset=utf-8")
            if path == "/api/soapyfang":
                return self.send(wb.soapyfang())
            if path == "/api/audio":
                seq, rate, body = wb.audio(int(q.get("after", 0)), q.get("monitor") != "1")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Seq", str(seq))
                self.send_header("X-Rate", str(rate))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", default="synthetic", help="synthetic | sigmf:PATH | soapy:ARGS")
    ap.add_argument("--rate", type=float, help="sample rate (synthetic 1.2e6, SoapySDR 2.4e6)")
    ap.add_argument("--center", type=float, default=440e6, help="first window centre, Hz")
    ap.add_argument("--gain", type=float, help="SDR receive gain, dB")
    ap.add_argument("--dbm-offset", type=float, help="dBm at full scale (calibration)")
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default) or 0.0.0.0 for the LAN")
    ap.add_argument("--port", type=int, default=5058)
    a = ap.parse_args(argv)
    src = open_source(a.source, rate=a.rate, center_hz=a.center, gain=a.gain, dbm_offset=a.dbm_offset)
    print(f"SoapyFang source: {src.describe()}", flush=True)
    print("building and booting the UV-21 port image ...", flush=True)
    wb = SoapyWorkbench(src)
    srv = uv21wb.ThreadingHTTPServer((a.host, a.port), handler(wb))
    print("SoapyFang UV-21 workbench on http://%s:%d/ (Ctrl-C stops it)" % (a.host, a.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        wb.board.close()


if __name__ == "__main__":
    main()
