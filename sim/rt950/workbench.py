"""The RT-950 Pro SURF image on SoapyFang in the browser: the emulated board (sim/rt950/board.py)
with both BK4829s hearing one I/Q source, its 240x320 screen, a clickable faceplate, the model
band's stations, the SoapyFang panel and the listener's audio.

    python3 -m sim.rt950                                     # a made-up 2 m band round 146.52 MHz
    python3 -m sim.rt950 --source sigmf:recordings/x.sigmf-meta
    python3 -m sim.rt950 --source pluto --gain 50 --minutes 30 \\
        --image rt950-surf2.bin --syms rt950-surf2.syms      # the CI artifact rt950-surf2

Receive only: no source opens a transmit stream, the page has no PTT, and the emulated board
writes neither flash. On hamlet the Pluto belongs to the bench runner, so a Pluto session runs as
its sim.soapyfang step with --radio rt950 (docs/bench-runner.md), not by hand. Without --image the
image is built here (`make -C radios/rt950 surf-image`, which needs the base firmware checkout at
./jm or $RT950_JM and arm-none-eabi-gcc). SPDX-License-Identifier: MIT"""
from __future__ import annotations

import os
import time

from sim import workbench
from sim.band import two_metre_band
from sim.soapyfang import page as sf

from . import board as bd
from .soapy import SoapyRT950Board

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")
KEYS = set(bd.MATRIX) | {"SIDE1", "SIDE4"}         # no PTT: SURF here is receive only
VFO_HZ = 146_520_000                            # the base firmware's VFO A with blank settings
BOOT_S = 3.0                                    # emulated seconds to SURF's first sweep (~2.5)


class RT950Workbench(sf.Soapy, workbench.Workbench):
    KEYS = KEYS
    SCALE = 2

    def _boot(self):
        with self.lock:
            old = self.board
            band = old.band if old is not None else two_metre_band()
            if old is not None:
                old.close(close_source=False)
            image, syms = self.built or (None, None)
            self.board = SoapyRT950Board(image, syms, band=band, source=self.source)
            if hasattr(self.source, "band"):
                self.source.band = self.board.band          # the page's station edits reach the I/Q
            self.board.run_for(BOOT_S)
            self.serial = ""
            self.wall0, self.virt0 = time.time(), self.board.seconds

    def state(self):
        with self.lock:
            self.serial = self.board.uart[-3000:].decode("latin-1")
            s = super().state()
            s["sweep_hz"] = self.board.chips[1].hz
            return s


def handler(wb):
    return sf.handler(wb, PAGE)


def main(argv=None):
    ap = sf.parser(__doc__)
    ap.set_defaults(center=VFO_HZ, port=5059)
    a = ap.parse_args(argv)
    if bool(a.image) != bool(a.syms):
        ap.error("--image and --syms go together")
    if a.image:
        built = open(a.image, "rb").read(), bd.symbols(a.syms)
    else:
        print("building the RT-950 SURF image ...", flush=True)
        built = bd.build()
    sf.serve(a, lambda src, b: RT950Workbench(src, built=b), handler, "RT-950 SURF", built)


if __name__ == "__main__":
    main()
