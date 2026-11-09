# 13. Never move a symbol clock backwards in a streaming DMA receiver

- From: `iflive` in the emulator (2026-11-06). The acquisition sweep wrapped its clock back
  11 steps (0.92 symbol), and the receiver then had about 300 us of samples to work
  through at once. The DMA ring lasts 186 us, so 15 counts were lost at each wrap. Moving
  forward to the same offset a symbol later costs nothing.
- Buys: no lost counts. The same applies to any module that re-times a stream (FT8, CW).
- Settled: yes, on the emulator (`tests/k6_live_emu.py` reports lost counts).
