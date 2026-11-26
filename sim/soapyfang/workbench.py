"""The UV-21 workbench (sim/uv21/workbench.py) on SoapyFang: the same page, with the board's RF
chip fed from an I/Q source, a SoapyFang panel (source, RSSI, noise, squelch) and the speaker in
the browser.

    python3 -m sim.soapyfang                                    # the synthetic band
    python3 -m sim.soapyfang --source sigmf:recordings/x.sigmf-meta
    python3 -m sim.soapyfang --source soapy:driver=rtlsdr --gain 30
    python3 -m sim.soapyfang --source soapy:driver=plutosdr,uri=ip:192.168.2.1 --gain 40
    python3 -m sim.soapyfang --source pluto --gain 50 --minutes 30 --image port.bin --syms port.syms

Receive only: no source opens a transmit stream. On hamlet the Pluto belongs to the bench runner,
so a Pluto session runs as its sim.soapyfang step (docs/bench-runner.md), not by hand. --image and
--syms take a prebuilt port image and its `llvm-nm` listing (hamlet has no thumbv7em target to
build one). Standard library and numpy only.
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import time

from sim.uv21 import workbench as uv21wb

from .board import SoapyUV21Board
from .page import Soapy, handler as soapy_handler, load_image, parser, serve


class SoapyWorkbench(Soapy, uv21wb.Workbench):
    def _boot(self):
        with self.lock:
            old = self.board
            band = old.band if old is not None else None
            if old is not None:
                old.close(close_source=False)
            image, syms = self.built or (None, None)
            self.board = SoapyUV21Board(image, syms, band=band, source=self.source)
            if band is None and hasattr(self.source, "band"):
                self.source.band = self.board.band          # the page's station edits reach the I/Q
            self.serial = self.board.run(until="boot done", limit=300_000_000)
            self.wall0, self.virt0 = time.time(), self.board.seconds


def handler(wb):
    return soapy_handler(wb, uv21wb.PAGE)


def main(argv=None):
    ap = parser(__doc__)
    a = ap.parse_args(argv)
    if bool(a.image) != bool(a.syms):
        ap.error("--image and --syms go together")
    built = load_image(a.image, a.syms) if a.image else None
    serve(a, lambda src, b: SoapyWorkbench(src, built=b), handler, "UV-21", built)


if __name__ == "__main__":
    main()
