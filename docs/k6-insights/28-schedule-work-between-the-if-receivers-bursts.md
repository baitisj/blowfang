# 28. Schedule work between the IF receiver's bursts, not after them

- From: the coded 4FSK thread, 2026-11-07 ([k6-if-fec.md](../k6-if-fec.md)). Tested on the
  emulator only.
- The ifrx receiver works in two bursts a symbol (half way through it, and at its end), about
  120 us each, against a DMA ring that lasts 183 us. Any other job begun just before a burst
  overruns the ring, even a 30 us one. In the coded session, a Viterbi step ran whenever the
  loop had caught up, and lost about 600 counts in 10 s. Run only at sample indexes 1-5 and
  10-14 of the symbol, it lost none, with the same idle time. The packet check runs at 1-4.
- It would buy: any module or image that adds work to the receiver (a decoder, a CW ID
  check, a screen update) can do so without a bigger ring. The rule is "start a job only
  where it ends before the next burst".
- Settled by: a bench session's `lost=` (it should be 0 on the K6 as in the emulator), and
  the same rule tried on iflive's own key checks.
