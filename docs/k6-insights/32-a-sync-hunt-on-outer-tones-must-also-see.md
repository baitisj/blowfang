# 32. A sync hunt on outer tones must also see modulation

- From: the PHY G thread, 2026-11-07 ([k6-if-fec.md](../k6-if-fec.md)). Settled on the model
  and on air (PR #120).
- On the half-baud tone grid an unmodulated carrier correlates well with both outer tones
  (600 Hz is a half turn a symbol, 2400 Hz two turns), so the hard decisions in the carrier
  before a lead and after a burst are runs of 0 and 3 with few inner tones, and a sync hunt
  that counts only inner tones as wrong found a sync word 78 symbols after a burst's last
  packet. The hunt now also counts an outer tone as wrong when the other outer tone's metric
  is within 3/4 of the winner's, which on a sent tone happens 5% of the time at the floor.
- It would buy: the same check for any future tone plan or sync word; a bare-carrier
  detector for free (both outer metrics high, no inner) if a module ever wants one.
- Settled by: 56 packets on air with no false sync, and the model at 45-55 dB-Hz.
