# 8. A softmodem on the GP0 IF instead of the chip's FFSK

- From: the softmodem thread, 2026-11-06 ([k6-if-modem.md](../k6-if-modem.md), simulation only).
- A matched filter on the complex IF beats the best FM discriminator by 5-6 dB, because it
  has no FM threshold. 4FSK at 1200 baud with soft rate-1/2 coding carries sotto 1200 about
  7.7 dB better than FFSK through a discriminator, and 2400 baud MSK is a dead end on the
  IC-7100.
- Buys: about 2.4x the range for live voice, and no chip bit-clock slips.
- Settled by: `gp0cap` with `tools/k6_ifcap.py`, which runs both detectors on the same
  captures at stepped front-end gain.
