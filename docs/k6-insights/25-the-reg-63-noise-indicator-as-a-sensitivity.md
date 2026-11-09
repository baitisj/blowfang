# 25. The REG_63 noise indicator as a sensitivity meter

- From: the coverage thread (2026-11-06, [k6-rxmap.md](../k6-rxmap.md)). Phase 0 read 0x0000
  on a strong carrier and 0x002a on an empty channel. If REG_63 rises smoothly as a carrier
  weakens, it measures FM quieting, which is close to what SINAD measures. Then sensitivity
  could be read off the IC-7100's power steps without a calibrated generator.
- Buys: a sensitivity number per band, and a squelch that opens on quieting, not on RSSI.
- Settled by: `k6_rxmap.py cal`'s `z` column against its levels (power steps at 440.850,
  on a dummy load).
