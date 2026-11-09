# 24. Time everything on the K6's DMA by the clock, not by assumption

- From: the first live-receiver run (2026-11-06). Channel 1's free-running rate was 5490
  kS/s under gp0dma, 5555 under gp0cap, and 5875 while iflive only polled. It moves with
  the CPU's bus traffic, and a module that times it in one loop and decodes in another is
  off by up to 7%.
- Buys: a receiver that acquires (it needs the rate to 0.05%) and a ring index that
  survives a stall. Any other DMA module (gp0dma, gp0cap, ft8rx, ft8wf) that measures its
  rate once has the same exposure.
- Settled (2026-11-07, 01:56 UTC rerun): `k=` read 5566 on every step of both modes,
  5563-5564 on steps with no lock, though the CPU sat 60% idle in 4FSK and 76% in FFSK. So
  while decoding, the rate is steady and barely depends on the load; 5875 was a polling-only
  loop. Measuring it as it runs is still what made the receiver lock.
