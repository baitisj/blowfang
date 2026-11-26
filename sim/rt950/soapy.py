"""The RT-950 Pro SURF image (sim/rt950/board.py) with both BK4829s hearing SoapyFang's I/Q: a
SigMF recording, the synthetic band, or a live SDR (sim/soapyfang/sources.py).

One source is the one antenna. Chip 0 (the listener) owns the window: the chip model keeps it
round chip 0's frequency and retunes the source only when chip 0 leaves it. Chip 1 (the sweeper)
reads its RSSI from the same spectrum wherever it falls inside the window. A sweep that leaves the
window retunes the source too, which costs chip 0 its spectrum for a moment; at SURF's default
500 kHz span the sweep stays inside a 1.2 MS/s window (or a Pluto's 2.4 MS/s), so it never does.
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import time

from sim.soapyfang.chip import ChipModel
from sim.soapyfang.sources import SyntheticSource

from .board import RT950Board


class SoapyRT950Board(RT950Board):
    def __init__(self, image=None, syms=None, band=None, source=None, **kw):
        self.source = source if source is not None else SyntheticSource(band)
        self.chip = ChipModel(self.source)
        super().__init__(image, syms, band=band, **kw)
        if source is None:
            self.source.band = self.band                # the board's band, edited live by a page
        self.window_misses = 0                          # chip 1 reads outside chip 0's window
        if self.source.live:
            self.chip.start()

    def level_dbm(self, chip, hz):
        self.chip.advance(self.seconds)
        if chip == 0:
            self.chip.tune(hz)
        elif hz and not self.chip.in_window(hz):
            self.window_misses += 1
        return self.chip.rssi_dbm(hz)

    def step(self, instructions=2_000_000, chunk=20_000):
        """as RT950Board.step; with a live source, never ahead of the wall clock"""
        super().step(instructions, chunk)
        if self.source.live:
            if not hasattr(self, "_pace"):
                self._pace = (self.seconds, time.monotonic())
            ahead = (self.seconds - self._pace[0]) - (time.monotonic() - self._pace[1])
            if ahead > 0:
                time.sleep(min(ahead, 0.05))

    def close(self, close_source=True):
        self.chip.stop(close_source)
