# 18. FT8 (and WSPR) from the receive IF

- From: the FT8/WSPR thread, 2026-11-06 ([k6-ft8-wspr.md](../k6-ft8-wspr.md)).
- Tested in simulation and on the emulated M0: `ft8wf` (3.8 KB) streams a 4-bit waterfall
  that the PC decodes; 75% at -16 dB through the modelled GP0 path. The whole decoder
  `ft8rx` is 7.2 KB and waits for a bigger module arena.
- Run 1 on air (2026-11-06): the IF offset read +11 Hz (with the AFC on; see run 5). The
  noise rms reached only 4.1 at the top gain step (the sim assumed 8), so expect the sim's
  floor-0.06 numbers (-16 dB 42%). No decodes: the ring overran (see 19).
- Run 2: no overruns, but no tones in the window. Run 3's raw-IF capture showed why: the
  sideband is upright, but the chip's AFC pulls a USB tone toward the IF's centre (time
  constant about 30 s). REG_73 bit 4 alone didn't stop it; ft8wf now retunes (RXON) after
  setting it, as REG_38/3B need.
- Run 4: the first decode (1 of 7). Run 5 (AFC off, then RXON) showed the true offset:
  the carrier is 145 Hz low (run 1's +11 Hz was the AFC's doing). Aimed at `--f0 1624`, the
  K6 decoded 6 of 7 frames at s=-16, the three 14 dB-down frames included. FT8 receive on
  the K6 works; the miss came 3 s after a steady tone, while levels recovered.
- Drift (K6 and IC-7100 together): 0.05-0.17 Hz/s on the bench. Ample for FT8; for WSPR
  it means 5-20 Hz over a frame, marginal.
- Untested: drift over temperature and over 10 minutes, which decides WSPR at 440 MHz;
  `ft8rx` on the radio (needs the arena).
- Buys: a weak-signal mode on the pager's own hardware, and a first user of a bigger arena.
- Settled by: a 10-minute IF log of a bare carrier from a cold start; the arena change.
