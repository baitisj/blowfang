# 9. More IF gain against the 1-bit floor

- From: the same study. GP0's sigma-delta adds its own noise. If the receiver's noise sits
  only "a few %" above it on GP0, 1.5-4 dB is lost.
- Buys: those dB back, by running REG_13 above the AGC table's top entry (for example
  0x03FF against 0x03BE) while the signal is weak.
- Settled by: `gp0cap` pass 0 (the floor at three gains) and steps 1 against 2.
