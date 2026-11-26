"""What every SoapyFang workbench shares, whichever radio it emulates (sim/soapyfang/workbench.py
the UV-21, sim/rt950/workbench.py the RT-950): the panel and its calls (/api/soapyfang, /api/audio),
the command line, and a session that closes its source however it ends. The radio's board has
.chip (sim/soapyfang/chip.py's ChipModel), tuned_hz(), fast_bus and close(close_source).
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import argparse
import os
import signal
import threading
import time

from sim import workbench

from .sources import open_source

PANEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.html")


def load_image(image, syms):
    """a prebuilt port.bin and its `llvm-nm port.elf` listing -> (image bytes, symbols)"""
    with open(syms) as f:
        table = {ln.split()[2]: int(ln.split()[0], 16) for ln in f if len(ln.split()) == 3}
    with open(image, "rb") as f:
        return f.read(), table


class Soapy:
    """mixed in before sim.workbench.Workbench: a board fed from self.source"""

    def __init__(self, source, step=400_000, built=None):
        self.source = source
        self.board = None
        self.built = built                      # (image, syms), or None for the board to build
        super().__init__(step=step)

    def reboot(self):
        with self.lock:
            self._boot()
            self.error, self.running = None, True

    def stop(self):
        """end the session: no more board steps (the next one would run in unicorn while Python shuts
        down: a segfault at exit), the chip's reader thread joined; the caller closes the source"""
        with self.lock:
            self.running = False
            if self.board is not None:
                self.board.close(close_source=False)

    def soapyfang(self):
        with self.lock:
            b, c = self.board, self.board.chip
            tuned = b.tuned_hz()
            return {"source": self.source.describe(), "live": self.source.live,
                    "center_hz": c.src.center_hz, "rate": c.rate, "tuned_hz": tuned,
                    "rssi_dbm": round(c.rssi_dbm(tuned), 1) if tuned else None,
                    "noise": c.noise_raw(), "glitch": c.glitch_raw(),
                    "squelch_open": c.noise_raw() <= 60, "retunes": c.retunes,
                    "fast_bus": b.fast_bus, "error": c.error}

    def audio(self, after, gated):
        seq, pcm = self.board.chip.pcm_after(after, gated)
        return seq, int(round(self.board.chip.pcm_rate)), pcm.tobytes()



def handler(wb, page):
    """the radio's page with SoapyFang's panel added, and the panel's two calls"""
    base = workbench.handler(wb, page)

    class H(base):
        def do_GET(self):
            path, _, query = self.path.partition("?")
            q = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
            if path == "/":
                html = open(page, encoding="utf-8").read()
                panel = open(PANEL, encoding="utf-8").read()
                return self.send(html.replace("</body>", panel + "</body>"), "text/html; charset=utf-8")
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


def _stop_on_term(signum, frame):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)       # once: a second one mustn't cut the clean-up
    raise KeyboardInterrupt


def parser(doc):
    ap = argparse.ArgumentParser(description=doc.split("\n\n")[0])
    ap.add_argument("--source", default="synthetic", help="synthetic | sigmf:PATH | soapy:ARGS | pluto[:URI]")
    ap.add_argument("--rate", type=float, help="sample rate (synthetic 1.2e6, SoapySDR 2.4e6)")
    ap.add_argument("--center", type=float, default=440e6, help="first window centre, Hz")
    ap.add_argument("--gain", type=float, help="SDR receive gain, dB")
    ap.add_argument("--dbm-offset", type=float, help="dBm at full scale (calibration)")
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default) or 0.0.0.0 for the LAN")
    ap.add_argument("--port", type=int, default=5058)
    ap.add_argument("--image", help="a prebuilt image (default: build it)")
    ap.add_argument("--syms", help="with --image: `llvm-nm` of its ELF saved to a file")
    ap.add_argument("--minutes", type=float, help="stop after this long (the runner's step sets it)")
    return ap


def serve(a, make_workbench, make_handler, name, built):
    """open the source, start the workbench and its page, and close everything on the way out
    (Ctrl-C, --minutes, or the runner's SIGTERM)"""
    try:
        import unicorn  # noqa: F401
    except ImportError:
        raise SystemExit("SoapyFang needs the unicorn emulator: sudo pacman -S python-unicorn"
                         " (Arch) or pip install unicorn")
    if a.source == "pluto" or a.source.startswith("pluto:"):
        from .pluto import open_pluto
        src = open_pluto(a.source, rate=a.rate, center_hz=a.center, gain=a.gain, dbm_offset=a.dbm_offset)
    else:
        src = open_source(a.source, rate=a.rate, center_hz=a.center, gain=a.gain, dbm_offset=a.dbm_offset)
    srv = wb = timer = None
    try:
        print(f"SoapyFang source: {src.describe()}", flush=True)
        print(f"booting the {name} image ..." if built else f"building and booting the {name} image ...",
              flush=True)
        wb = make_workbench(src, built)
        srv = workbench.ThreadingHTTPServer((a.host, a.port), make_handler(wb))
        if a.minutes:
            timer = threading.Timer(a.minutes * 60, srv.shutdown)
            timer.daemon = True                         # an early stop mustn't wait for it
            timer.start()
        end = f", until {time.strftime('%H:%M:%SZ', time.gmtime(time.time() + a.minutes * 60))}" if a.minutes else ""
        print(f"SoapyFang {name} workbench on http://{a.host}:{a.port}/{end} (Ctrl-C stops it)", flush=True)
        # the bench runner stops a step with SIGTERM (a cancel, or its time limit): end as Ctrl-C
        # does, so the finally below still closes the source and parks the Pluto's LO
        signal.signal(signal.SIGTERM, _stop_on_term)
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if timer:
            timer.cancel()
        if srv:
            srv.server_close()
        if wb:
            wb.stop()
        src.close()
        print("SoapyFang stopped; the source is closed", flush=True)
