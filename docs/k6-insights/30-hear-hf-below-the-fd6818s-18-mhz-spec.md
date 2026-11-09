# 30. Hear HF below the FD6818's 18 MHz spec

- From: the HF thread, 2026-11-07 ([k6-hf.md](../k6-hf.md)). Untested on the radio.
- 18 MHz was where the coverage run started, not a measured limit. Aura's tune path has no clamp,
  and the low end is the VCO's bottom over its largest divider. UV-K5 forks claim 14-15 MHz, which
  would reach 20 m. `rxmap` segments `hf` and `hflo` scan it with the module already on the radio.
  `hfprobe` tries the front-end path pins (PA13/PA14) and REG_3E/REG_1F at exact-frequency
  markers (clock harmonics, WWV, CHU).
- It would buy: HF listening with no hardware change. 20 m FT8 through ft8wf's GP0 software
  demodulator would be the showpiece.
- Settled by: the clock birdies at 12 and 13 MHz turning up at their own frequency, or not.
