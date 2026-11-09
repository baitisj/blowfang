# 45. Set the K6's clock from APRS traffic

- From: Jeff, 2026-11-08 (thread "Set K6 clock from APRS"). Built and emulator-tested
  (`c/k6/mod/aprsclk.c`, docs/k6-aprs-clock.md); not on air. Needs the 11,776 B arena of a
  voice image (5.0 KB code, 2.3 KB state).
- Aura's own APRS is send-only (a beacon on the tone oscillator), so the receiver is new: IF tap,
  CORDIC discriminator, Bell 202 correlators, DPLL, HDLC, then `DDHHMMz`, `HHMMSSh` and
  `$GPRMC` stamps voted with Marzullo's algorithm, one vote a station.
- It would buy: UTC on the K6 with no GPS and no bench, so WSPR and FT8 modules can start on
  their slots by themselves (today the bench's CW ID is the sync mark), once the core keeps a
  clock between modules (API level 8, built 2026-11-09).
- Next: a narrower filter before the discriminator might move the FM threshold (decodes stop
  near 51 dB-Hz on the model); an IC-7100 recording of 144.390 would count the local stamps.
- Settled by: a few minutes on 144.390 with a voice image, the `clk` lines against NTP.
