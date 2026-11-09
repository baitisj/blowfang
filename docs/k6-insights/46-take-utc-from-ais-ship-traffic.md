# 46. Take UTC from AIS ship traffic

- From: Jeff, 2026-11-09 (thread "Get K6 time from AIS ship traffic"), after the APRS clock
  found no local stamps. Host receiver `sim/k6if/ais.py` and capture module
  `c/k6/mod/aiscap.c` built and emulator-tested (docs/k6-ais.md); not on air.
- AIS shore stations send the date and second every 10 s (message 4), and a ship's report
  often names the TDMA slot it was sent in. Slots are locked to the UTC minute, so a burst's
  start flag gives UTC to well under a millisecond.
- It would buy: a clock far tighter than APRS's 1 s stamps, heard all day near the coast.
- Run 1 (DMA): 739 bursts in 10 min, none decoded, the best ~5 dB short; the limit is the
  1-bit tap sampled at 5.5 MS/s. aiscap v2 polls it at 22.9 MS/s (~6 dB on the model).
- Run 2 (polled, 03:16Z): decoded a US base station's message 4 seven times in 10 min, UTC
  matching NTP; its slot numbers agree across 100 s to ~2 ms.
- aisclk (`c/k6/mod/aisclk.c`, receiver `c/k6/aisrx.h`): decodes on the M0 (9 of run 2's 10
  frames, flags within a count of the host's) and sets UTC from message 4 and slots; the
  emulator puts each flag within 0.2 ms. Jeff (03:34-03:43Z): BLOWFANG needs both clocks,
  AIS first, APRS when no AIS is heard (aisclk gives up after 2 min).
- Run 3 (04:10Z): aisclk set the K6's clock on the air, 52 frames and 7 slot fixes in 15
  min. Bursts sit on the slot grid to 0.1 ms; the K6's own clock runs 134 ppm fast and wanders
  ±100 ppm, so aisclk now snaps to the slot grid on every burst (~2 ms between fixes). RDS
  (no pilot in 104.3's IF) and ACARS (nothing in 2 min) came up empty so far.
- Run 4 (04:37Z): with snapping, 54 fine fixes from 55 frames, each burst's millisecond
  steady to ±1 ms for 14 min.
- Run 5: the RSSI path is ±5 kHz, but the GP0 tap passes stations 50-60 kHz below tune (the
  host decodes message 4 from 50 kHz off). RDS stays out: the tap is one real low-IF stream.
- Run 6: tuned to 162.009 MHz, one capture holds both channels (host: 26 A + 15 B frames).
  aisclk set the level 8 core clock on air (clkget inside its bound 15 min later).
- aisships (Jeff chose to build it, 07:54Z): tuned between the channels, each burst decoded
  at both places; 16 stations, distance and bearing from the first base station, NMEA out.
  Run 7 (08:29Z): 41 frames on air, all valid !AIVDM, but only the base station (no ships;
  the floor rose ~25% after a minute and B was lost).
