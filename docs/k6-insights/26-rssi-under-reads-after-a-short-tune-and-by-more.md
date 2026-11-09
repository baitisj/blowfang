# 26. RSSI under-reads after a short tune, and by more for weak signals?

- Phase 0's curves come from a -20 dBm carrier. rxmap's quick pass (3 ms) only ranks the
  channels in a bin, then re-reads the winner at 30 ms. If a weak signal (-110 dBm) ramps
  slower than the empty channel next to it, the quick pass ranks it below the noise.
- Settled by: a map run with `--quick-us 12000` over the same segment, compared bin by bin.
