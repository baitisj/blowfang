"""The UV-21 Pro port image on the emulated board (sim/uv21/board.py) with its RF chip answered by
SoapyFang's chip model from I/Q, instead of the model band's RSSI arithmetic.

- REG_67, REG_65, REG_63 and REG_0C bit 1 come from ChipModel; every other register reads what
  was written, as in UV21Board.
- Virtual clock (default): the chip pulls I/Q up to the board's emulated time, so a SigMF file or
  the synthetic band plays at the firmware's own pace, the same on every run. Live (an SDR): the
  chip's reader thread keeps up with the SDR, and the workbench paces the board to the wall clock.
- fast_bus: the firmware's register read (Fd6818::read_reg) and its microsecond waits
  (hal::delay::us) are answered at their entry by a hook, with the time they would take added to
  the virtual clock, instead of bit by bit through the GPIO model (about 300 Python callbacks a
  register read). Writes still go bit by bit, through the image's own receive-only gate, so the
  transmit checks see exactly what the image sends.
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import time

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0

from sim.uv21.board import SCLK, UV21Board

from .chip import ChipModel
from .sources import SyntheticSource

READ_REG_US = 52                # read_reg: 52 waits of BIT_DELAY_US (1 us) in fd6818b.rs


def _sym(syms, *parts):
    """the one symbol whose (mangled) name holds every part"""
    hits = [a for n, a in syms.items() if all(p in n for p in parts)]
    return hits[0] & ~1 if len(hits) == 1 else None


class SoapyUV21Board(UV21Board):
    def __init__(self, image=None, syms=None, band=None, source=None, fast_bus=True, **kw):
        super().__init__(image, syms, band=band, **kw)
        self.source = source if source is not None else SyntheticSource(self.band)
        self.chip = ChipModel(self.source)
        self.fast_bus = False
        self.fast_reads = 0
        if fast_bus:
            rd = _sym(self.syms, "fd6818b", "8read_reg")
            us = _sym(self.syms, "5delay2us")
            if rd and us:
                self.mu.hook_add(UC_HOOK_CODE, self._fast_read, begin=rd, end=rd)
                self.mu.hook_add(UC_HOOK_CODE, self._fast_us, begin=us, end=us)
                self.fast_bus = True
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

    # ---- the fast bus ----
    def _return(self, uc):
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)

    def _fast_read(self, uc, address, size, ud):
        a = uc.reg_read(UC_ARM_REG_R0) & 0x7F
        uc.reg_write(UC_ARM_REG_R0, self.reg_value(a) & 0xFFFF)
        self.cycles += READ_REG_US * (SCLK // 1_000_000)
        self.fast_reads += 1
        self._return(uc)

    def _fast_us(self, uc, address, size, ud):
        us = uc.reg_read(UC_ARM_REG_R0)
        self.cycles += max(1, us * (SCLK // 1_000_000))
        self._return(uc)

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
