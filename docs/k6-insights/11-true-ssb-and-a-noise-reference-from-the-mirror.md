# 11. True SSB and a noise reference from the mirror band

- From: Jeff found the chip's "USB" is DSB. Its USB mode is zero IF, so the sidebands fold.
  On GP0's 16.9 kHz IF they don't, so the K6 can tell the sidebands apart in software. The
  empty side also gives a live noise level for soft decisions and squelch.
- Settled by: a capture of an SSB voice signal through `gp0cap`, demodulated on the host.
