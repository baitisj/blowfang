# 22. gp0dma's IF reading was biased high

- From: the ifcal thread (2026-11-06), in the emulator. `run()` scaled the IF by
  `cyc >> 10`, floored, so it read up to 1/1000 high: about 17 Hz at BLK 940, up to 3.4 Hz at
  the sweep's 4688. It now rounds and scales with 8x the time resolution; ifcal reads a
  -145.5 Hz offset as -145.1 in the model.
- Buys: an IF offset good to about half a Hz, which FT8 (6.25 Hz tones) and WSPR (1.46 Hz)
  both need. Earlier `if=` readings from gp0dma and gp0sweep are a few Hz high.
- Settled by: ifcal on the radio against `tools/k6_ft8.py tone`'s -145 Hz.
