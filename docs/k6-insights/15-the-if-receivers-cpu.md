# 15. The IF receiver's CPU

- From: `iflive` on the emulator at 1.5 cycles an instruction: 4FSK leaves 41% idle and FFSK
  51%. That is about twice the study's estimate, so it doesn't yet leave room for the sotto
  decoder (55-63%).
- Buys: the live 4FSK voice pager.
- Profiled (2026-11-06): the filter took 21%, counting and polling about 21%, the template
  sums 9%. A mirrored ring, packed taps and moving the key check off the receiver's bursts
  took idle to 52% (4FSK) and 63% (FFSK); docs/k6-if-modem.md has the table.
- Settled by: the bench's `i=` numbers. The decoder still wants about 10 more points, from
  the counting (an unrolled loop, about 6% in `gp0dma`'s estimate).
