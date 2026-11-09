# 33. Rate 5/6 on the 600-2400 Hz tones

- From: the PHY G thread, 2026-11-07. Not built.
- The new tones made 0 raw errors in 80,400 bits on air and rate 3/4 decodes 13-14 of 14.
  The K=7 code's rate 5/6 pattern (133: 1 0 1 0 1 / 171: 1 1 0 1 0, 6 bits each 5 steps)
  would give 2064 x 3/5 = 1238 coded bits a packet, not a whole number of 86-bit blocks, so
  it needs a 14-block padding rule or a block size of 1238/2 = 619 (prime: no). The nearest
  clean fit is 6 bits each 5 steps over 1032 steps = 206.4 groups, so the tail would need
  8 steps' worth of extra zeros (1040 steps, 1248 bits, still not a multiple of 86) or a
  block of 78 bits (16 blocks of 78 = 1248). About 2000 bit/s net, 1.3x F/1800.
- It would buy: the first K6 link clearly faster than the chip's modem, with soft decoding.
- Settled by: the model at about 51 dB-Hz for every packet, and a bench run as for rate 3/4.
