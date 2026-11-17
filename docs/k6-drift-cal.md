# K6 crystal drift calibration (refcal)

Jeff, 2026-11-17: "Temperature drift is definitely real on the K6 and we need to think about how
to manage this." The K6 has a plain 26 MHz crystal and no TCXO. That one crystal drives the whole
synthesizer, so its error is the same in ppm on every band: 1 ppm is 440 Hz at 440.850 MHz. On
2026-11-16 the K6 went from decoding bench uploads at 12:24 PM PT to failing every packet by
3:50 PM PT as the garage cooled. That happened with both the calibrated Pluto and the IC-7100
as the transmitter, and it survived a power-cycle. (At 00:39Z the K6 heard nothing at any offset,
so the size of that night's error is still unmeasured.)

The plan, with its reference ranking and bench steps, is the Claude Doc "K6 frequency drift
calibration" (https://claude.ai/artifact/Wu7Xk8eRnTW5VaRHtL9aYG). This file covers what is
built.

## Pieces

| piece | where | what |
|---|---|---|
| SURF custom range | c/k6/mod/surf.c, docs/k6-surf.md | BAND's USER stop (470-608 MHz until V types another) and ±10/±25 MHz spans, used to see which references hamlet hears |
| refcal | c/k6/mod/refcal.c | scans the references, measures the crystal error against the strongest few with null-seeking, reads the MCU's temperature, and optionally trims REG_3B |
| ifcal | c/k6/mod/ifcal.c | the bench version: a bare carrier on 440.850, its IF offset into the K6CL record |
| Pluto survey | hamlet ~/blowfang-work/runs/refcal-survey-20261117/ | each pilot's true frequency (2 s captures, 0.5 Hz bins) |

## Facts it rests on

- **REG_3B is ours to set.** Aura never writes REG_3B. It corrects the stock calibration byte only
  through the frequency word (`fd6818.rs` `set_frequency_hz`, a 17-entry table). A module's
  REG_3B write is taken at the next RX on and then holds for everything the radio does, receive
  and transmit, until power-off. One step is 5 Hz of the crystal's 26 MHz (0.192 ppm): 85 Hz at
  440.850 and 90 Hz at 470.309. A larger REG_3B moves the LO down (xtalstep, 2026-11-06).
- **The measurement.** gp0dma's IF tap with the AFC off (REG_73 bit 4) gives a carrier's mean IF
  to about 0.1 Hz over 32 blocks of 52 ms. The tap's time base is the MCU's RC clock, measured
  270-540 ppm slow against the crystal (docs/k6-characteristics.md). At the 16.9 kHz IF that is
  4-9 Hz, about 10-20 ppb at 470 MHz, which is small next to one 192 ppb step.
- **Null-seeking.** At low SNR the noise in the IF filter pulls a plain mean toward the filter's
  middle, which is the nominal IF (26 MHz / 1536 = 16927.1 Hz). So refcal steps REG_3B until
  the carrier sits there and reads the residual, which is unbiased.
- **ATSC pilots.** Each pilot is a bare carrier 309.440559 kHz above its channel's lower edge, on
  all the time. On 2026-11-17 the Pluto found RF 14 (470.309, 35 dB SNR on a stub), 20, 26 and
  35 agreeing within 0.1 ppm, and RF 16, 19, 22 and 23 off by 0.6 to 6 ppm. RF 17 has no pilot
  (ATSC 3.0?). So refcal takes the median of several pilots. The K6 tunes on a 10 Hz raster,
  0.56 Hz below a pilot, and refcal takes that off.
- **The temperature.** The KD32F328's ADC (0x40012400) has CCR bit 23 TSEN and bit 22 VREFEN,
  and channels 16 and 17 (STM32F0 style). Aura enables and calibrates the ADC for the battery
  (PA1, channel 1). There is no factory calibration, so the raw sums are a relative index for a
  drift table. The MCU clock against the crystal is a second thermometer (an idea from the
  2400-baud thread).

## refcal

Upload it like any module (`tools/k6_upload.py c/k6/mod/refcal.c --api-level 4 --key-file
~/.config/sotto/k6-mod.key --rounds 1`). It receives only.

1. The temperature and VREFINT sums.
2. The scan: 23 ATSC pilots (RF 14-36) and the 7 weather channels. For each one it takes a
   settled RSSI on the reference and on the quieter of ±400 kHz, with the narrow filter (about
   2 s in all).
3. With the AFC off, on the strongest reference that is REFCAL_SNR (10) dB over its side: the
   mean IF, then the mean IF again at REG_3B + 6. The move must be within 40-160 % of six steps
   at that frequency, and its sign says which side the chip mixes on.
4. Null-seeking: REG_3B is set where the carrier should land on the nominal IF, and the IF is
   read again, up to three times.
5. The next strongest references (REFCAL_NREF, 4 in all) are each read once at the null. The
   answer is the median.

```
refcal t=27731 v=24640 3b=5880 3c=4f88
refcal s 470309.44 -71 -104
refcal m 470309.44 if=16926.3 n=32 sp=4.1 k=+0
refcal step 470309.44 d=-539.8 g=541.6
refcal r 470309.44 ppb=-335 res=+1.2 k=-2
refcal ppb=-335 n=3 k=-2 t=27731 dry
```

`ppb` is the crystal's own error, + meaning fast (every LO that much high). It is the same
whichever side the chip mixes on. `k` is the REG_3B trim that cancels it. The default build is a
dry run that puts REG_3B back. `REFCAL_APPLY` leaves the trim in place until power-off.
`REFCAL_SCAN_ONLY` stops after the scan. `REFCAL_HZ` measures one frequency.
`REFCAL_KNOWN` lists references the Pluto found off nominal, as {kHz × 100, 0.1 Hz} pairs.
Nothing is written to flash yet.

If the probe's move isn't one step's worth on the strongest reference, refcal probes the next
strongest, up to REFCAL_PROBES (3), and returns -12 only when none passes.

### First bench run (2026-11-17 04:46Z, K6 on its stub)

refcal reached the K6 (38/38 chunks from the Pluto at -63 dBm) and kept nothing (-12). RF 14 read
-105 dBm against -130 beside it, but its mean IF was 20031.6 Hz (3.1 kHz above nominal, spread
1894 Hz), and six steps moved it +146.7 Hz against +542.7 expected (27 %). The weather channels
were all at the floor (-111 to -114). Either the pilot sits under its own 8VSB data on the stub, or
the K6 is about 6.6 ppm off and the pilot sat at the IF filter's edge. The first MCU temperature
reading: t=29906 v=24081. The Pluto read pilots 14, 26 and 35 within 0.06 ppm of each other, and
itself 0.92 ppm slow. Next time, the K6 goes on the big antenna (Jeff).

### The Pluto relay

The Pluto checks itself against the pilots (`pluto_bench.py rx`, then the pilots' offsets), then
sends refcal built with `REFCAL_HZ 440850000`, followed in the same send by about 25 s of PSK31
'AG7EW TEST' at a low level. The module starts as its last chunk checks, so it measures that
carrier at 50 dB SNR. Built with REFCAL_HZ, refcal doesn't wait for the upload's carrier to go
(gp0dma's 30 s wait for RSSI under -80 dBm, GP0_NO_WAIT): on 2026-11-17 05:06-05:14Z that wait
outlasted the tail in four tries, and refcal then scanned an empty channel (-6). A mean IF with
too few good blocks now says so (`refcal m 440850.00 few n=3 last=17012`). A carrier p ppm low looks like the K6 p ppm fast, so the K6's own error is
refcal's ppb less the Pluto's (+ slow) ppb.

Tests: tests/test_k6_refcal.py (emulated M0, gp0dma's DMA model, a receiver that follows the
tunes, an IF that REG_3B moves, the ADC). It covers the scan, the probe on either mixing side,
null-seeking from 4 ppm off, the median leaving out a pilot 1 kHz off, a weather station with
voice, a dry run against REFCAL_APPLY, no reference, an ADC left off, the relay's 440.850 carrier,
and a reference failing the probe (the next one taken; all failing keeps nothing).

## Still to do

- Bench (RX only, when the coordinator hands over the bench): a SURF survey of USER and MAR/WX,
  a refcal dry run on RF 14, then refcal and the pager offset test back to back three times as the
  garage cools. It passes if refcal's ppb predicts the 440.850 error within 100 Hz.
- A drift table: (temperature, ppb) points in the K6CL record's sector, after the bench runs show
  the curve. This needs Jeff's OK, since it is a new use of SPI flash.
- Running refcal at boot or on CRC failures would need an image change, which is Jeff's call for
  the flash batch.
