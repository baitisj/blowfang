"""The UV-21 Pro port image on the emulated board (sim/uv21/board.py) with its RF chip answered by
SoapyFang's chip model from I/Q, instead of the model band's RSSI arithmetic.

- REG_67, REG_65, REG_63 and REG_0C bit 1 come from ChipModel; every other register reads what
  was written, as in UV21Board.
- Virtual clock (default): the chip pulls I/Q up to the board's emulated time, so a SigMF file or
  the synthetic band plays at the firmware's own pace, the same on every run. Live (an SDR): the
  chip's reader thread keeps up with the SDR, and the workbench paces the board to the wall clock.
- The fast register-read path (fast_bus) comes from UV21Board.
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import time

from sim.uv21.board import UV21Board

from .chip import ChipModel
from .sources import SyntheticSource


class SoapyUV21Board(UV21Board):
    def __init__(self, image=None, syms=None, band=None, source=None, fast_bus=True, **kw):
        self.source = source if source is not None else SyntheticSource(band)
        self.chip = ChipModel(self.source)
        super().__init__(image, syms, band=band, fast_bus=fast_bus, **kw)
        if source is None:
            self.source.band = self.band                # the board's band, edited live by the page
        if self.source.live:
            self.chip.start()

    # ---- the chip ----
    def level_dbm(self, hz, t=None):
        self.chip.advance(self.seconds)
        return self.chip.rssi_dbm(hz)

    def reg_value(self, a):
        if a in (0x67, 0x65, 0x63, 0x0C):
            self.chip.advance(self.seconds)
            self.chip.tune(self.tuned_hz())
            v = self.chip.read_reg(a, self.regs)
            if v is not None:
                return v
        return super().reg_value(a)

    def step(self, instructions=2_000_000):
        """as UV21Board.step; with a live source, never ahead of the wall clock"""
        out = super().step(instructions)
        if self.source.live:
            if not hasattr(self, "_pace"):
                self._pace = (self.seconds, time.monotonic())
            ahead = (self.seconds - self._pace[0]) - (time.monotonic() - self._pace[1])
            if ahead > 0:
                time.sleep(min(ahead, 0.05))
        return out

    def close(self, close_source=True):
        self.chip.stop(close_source)
