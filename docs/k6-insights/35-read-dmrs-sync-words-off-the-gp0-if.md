# 35. Read DMR's sync words off the GP0 IF

- From: the DMR thread, 2026-11-08 ([k6-dmr.md](../k6-dmr.md)). **Works on air** (run 3,
  07:02Z: a real DMR key-up muted 20 ms after carrier-up, the analog ID then played).
- DMR's 48-bit syncs use only the +-1944 Hz symbols and are balanced, so a normalised
  correlation on the zero-crossing discriminator, at 2 points a symbol, finds them at
  rho 0.95-0.99 where FM voice, FFSK and tones stay under 0.77. Two hits a whole number of
  30 ms slots apart confirm DMR. `dmrdet` mutes the speaker on it (3 KB code, 26% CPU on
  the K6). A repeater's output doesn't pulse (both slots go out), so RSSI alone can't do it.
- What the K6 needed on real signals: the pager's AGC turns a -85 dBm carrier's IF down
  to only 4-5 dB over GP0's own noise. Freezing REG_13 at 0x03FF (REG_7E bit 15) gives
  ~10 dB, and a 2nd-order band-pass ahead of the crossings takes out the sigma-delta noise
  above the band (bare crossings 0.7-0.8, band-passed 0.95-0.99). Both likely help any
  other GP0 receiver at modest signal levels (`dmrcap` captures and `sim.k6if.dmr cap
  --diag` measure it).
- It would buy: a K6 that mutes DMR (and later YSF/P25) buzz, an EXPLORE "DMR" mark, and a
  way into DMR metadata (colour code, talkgroup, source ID) with the same front end.
- Settled by: `mod-dmrdet.bin` on 440.925 with the IC-7100 recording alongside (done).
