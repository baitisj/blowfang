# K6: the front end's band switch as an attenuator (`fesw`)

Receive only. Started 2026-11-11, after run L of the DMR sync work (docs/k6-dmr.md) left a
caution standing: a signal made weak by taking gain off inside the chip is not a weak station,
and nobody can visit the radio to unplug the antenna or fit an attenuator.

## The idea

The K6 has two receive front ends, VHF and UHF. The MCU picks one with two pins (Aura's
`board/rf.rs`: PA13 high is the UHF path, PA14 high the VHF path, exactly one high while
receiving, both low for TX and power-down). `api->tune` sets the one the frequency wants.

A RAM module can pick the other one, or neither. The signal then reaches the chip only through
what leaks past the switch: a loss **ahead of the receiver**. The receiver's own noise stays
where it was and the signal sinks towards it, as a far station's does. Two uses:

- **The 2 m noise question** (docs/k6-aprs-clock.md): 144.390 MHz at hamlet has a floor of
  -102 dBm, against -117 to -123 dBm on 6 m and 70 cm. If that floor stays with the front end
  off, the noise is made behind the switch, in the chip or its IF. If it falls, it comes from
  the VHF path: the antenna, its cable, or the VHF front end's own amplifier, whose noise
  goes with the path as well. (The first plan said "then it comes in from the antenna side";
  that was too much.) Unplugging the antenna tells the amplifier from the antenna and cable;
  that is on Jeff's hands-on list, shelved.
- **True weak-signal tests with no hands**: the DMR repeater on 440.925 MHz reads -83 to
  -87 dBm here, so some tens of dB of loss put it at the edge run L found (first put at -115 dBm, -122 dBm
  after this probe's run 1, below). The
  IC-7100's test signals on 440.850 MHz (-21 dBm at RF power 0) come down by the same amount.

What is not known before the first run: how many dB the wrong path and the open switch lose, on
either band. That is the probe's first answer.

## The module

`c/k6/mod/fesw.c` (1,020 bytes, level 2). `FE_ROUNDS` (20) times round four frequencies:

- 144.390 MHz, the 2 m APRS channel (the floor in question)
- 162.550 MHz, NOAA weather radio (a steady carrier on the VHF path)
- 440.925 MHz, the DMR repeater's output (its floor, and a key-up if one falls in the run)
- 440.850 MHz, the bench's channel (the IC-7100's test carrier, on the UHF path)

On each, three states of 0.75 s: `s=0` the path the firmware picked, `s=1` the other path,
`s=2` neither. A state is the pins, 100 ms to settle, then 64 readings 10 ms apart of the RSSI
(REG_67), the noise meter (REG_65) and the glitch meter (REG_63):

    fe 3 f=440925 s=1 t=41234 r=-104 -101 -99 n=41 g=18

Round, kHz, state, ms, RSSI lowest / mean / highest, mean noise, mean glitch, and an `x` if the
pins were not as set when the readings ended. The chip's AGC stays as the radio has it. A run
is about three minutes; a key ends it.

Care with the pins: after each `api->tune` exactly one of the two must be high, or the module
says `fesw pins=` and returns -5 with nothing written. A change clears before it sets, so the
two are never high together. The firmware's choice is put back after every frequency and
before the return. The module writes GPIOA's BSRR and BRR, bits 13 and 14 only, and no
register of the chip.

Report: `python3 -m modem.fesw LOG`. A frequency whose own-path level moves by more than
20 dB over the rounds (the test carrier keyed for part of the run) is reported twice, with and
without the carrier.

Tests: `tests/test_k6_fesw.py` (the module on the emulated M0 with a model of the pins).

## Run plan

1. Upload `fesw` to the K6 (440.850 MHz, RF power 0).
2. From about 60 s after `fesw start`, the IC-7100 sends PSK31 `AG7EW TEST` on 440.850 MHz at
   RF power 0 for about 45 s (four or five rounds see it). No other transmission.
3. Read the log with `modem.fesw`.

## Results

### Run 1 (2026-11-11 17:32:06 to 17:35:08Z, image 6f30645)

The K6 only listened. The IC-7100 sent three times, all on 440.850 MHz at RF power 0 with
ident: the CW ID that wakes the K6 (17:31:39Z), the upload (17:31:56Z, 6.3 s) and PSK31
`AG7EW TEST` (17:33:09 to 17:33:55Z). 240 lines, none marked `x`, no `fesw pins=`, "fesw end
n=240", ret=240, stack_free=408 B. The lines are `tests/data/k6/fesw-1-20261111.log.gz`.
The filter was the narrow one and the AGC the radio's own. dBm here is the K6's meter, which
nobody has calibrated.

| MHz | Own path | Other path | Neither | Loss, other and neither |
| --- | --- | --- | --- | --- |
| 440.850, the IC-7100's carrier (5 rounds) | -21 dBm | -69 | -73 | 48 and 52 dB |
| 440.850, no carrier (15 rounds) | -132 | -137 | -151 | |
| 440.925, the repeater silent (20 rounds) | -132 | -135 | -151 | |
| 144.390 (20 rounds) | -111 | -142 | -154 | |
| 162.550 (20 rounds) | -105 | -130 | -146 | |

(Each figure is the median over the rounds of a state's mean RSSI.)

- **The switch is an attenuator of about 50 dB.** A -21 dBm signal on 70 cm reads -69 dBm
  through the VHF path and -73 dBm with neither path on. The discriminator stayed quiet in
  all three (noise meter 51 to 54 against 66 to 68 on an empty channel).
- **The chip's own floor reads -151 to -154 dBm.** That is the reading with neither path on.
  The UHF front end lifts it to -132 dBm, 19 dB of front-end noise over the chip's, which is
  how a receiver should be: the front end sets the sensitivity on 70 cm. (-132 dBm is about
  the thermal noise of a 12.5 kHz channel, so the meter probably reads a few dB low.)
- **On 2 m the VHF path delivers 21 dB more noise than the UHF path does on 70 cm**: -111
  against -132 dBm, the same filter, the same minute. It was steady, -113 to -109 dBm in 17
  of the 20 rounds, not bursts.
- **That noise is not made in the chip or behind the switch.** With the VHF path off it is
  gone: -142 dBm through the UHF path and -154 dBm, the chip's own floor, with neither. So
  it enters at the VHF front end or ahead of it: the antenna, the cable, or the VHF
  amplifier itself. This run cannot tell those apart, because switching the path off takes
  the amplifier's own noise away with the antenna's.
- **Hints, no more, that it is picked up and not the amplifier's**: the VHF floor has read
  -91 dBm (2026-11-07, the stock antenna beside the PC, docs/k6-rxmap.md), -102 dBm
  (2026-11-10) and -111 dBm now, and an amplifier's own noise does not move by 20 dB. But
  those three were not taken alike (other filters, other read-outs), so it stays a hint.
- **Real signals on 144.390**: rounds 4 and 5 (17:32:43 and 17:32:52Z) read -97 and -98 dBm
  with the own path, 14 dB over the floor, most likely packets. Round 0's first state
  (-105 dBm, 0.8 s after the start) is the receiver settling.
- **162.550 MHz**: -105 dBm with the own path and the discriminator partly quiet (noise 64,
  glitch 15 against 26 on noise): the weather station, weak, in the VHF floor. With neither
  path on it reads a steady -146 dBm, 8 dB over the chip's floor, with the discriminator
  fully quiet (noise 53, glitch 4). Either the station's leak past the open switch or a
  carrier of the radio's own on that frequency; not settled.
- **Side effects of the 70 cm carrier**: while the IC-7100 sent, 440.925 (75 kHz off) rose
  by 6 dB, 144.390 through the UHF path rose by 9 dB and 162.550 through the UHF path fell
  by 5 dB. The own-path readings on VHF did not move.
- No key-up of the DMR repeater fell in the run.

**For run L's figures** (docs/k6-dmr.md): this run reads 440.925's floor directly, -132 dBm
with the AGC on. Run L's ratio gives the same: the repeater at -83 to -87 dBm stood 48 dB over
the floor, so the floor is near -133 dBm. Run L's text put the edge at -115 dBm by adding
10 dB to the floor as it read at one fixed gain (-125 dBm), where the signal read 8 dB high
too. On one meter the edge (10 dB over the floor) is near **-122 dBm** and full yield (24 dB
over) holds to about **-108 dBm**.

**As a weak-signal test**: neither state is quite a weak station, since both take the UHF
amplifier's noise away with the signal. With neither path on, the repeater would stand near
-137 dBm over a floor of -151 dBm, 14 dB over it, which is inside the gap run L left
between 24 and 10 dB. Through the VHF path it would stand near -133 dBm at a floor of
-135 dBm, under the edge.

### `fesw_vhf` (2026-11-12, the module built from 3ce8f73): the 2 m floor by frequency and hour

The same probe on fifteen frequencies of the VHF path and 440.925 MHz, five rounds in three
minutes, run twice around run N (docs/k6-dmr.md): 01:37:13 to 01:40:15Z and 03:13:53 to
03:16:55Z, and once more at 03:32:22 to 03:35:25Z (not in the table: within a dB of the
second everywhere). The K6 only listened. The IC-7100 sent a CW ID and the upload before each, on
440.850 MHz at RF power 0, and nothing during. Each: "fesw end n=240", ret=240,
stack_free=408 B, no line marked `x`. dBm is the K6's uncalibrated meter, the narrow filter,
the radio's own AGC.

| MHz | Own path, 01:37Z | Own path, 03:14Z | Other path | Neither |
| --- | --- | --- | --- | --- |
| 136.000 | -118 | -118 | -147 | -154 / -153 |
| 140.000 | -108 | -108 | -144 | -153 |
| 143.000 | -108 | -111 | -130 | -131 |
| 144.200 | -107 | -111 | -142 | -153 / -154 |
| 144.340 | -107 | -111 | -142 | -153 / -154 |
| 144.390 | -107 | -111 | -142 | -153 / -154 |
| 144.440 | -107 | -111 | -141 | -153 / -154 |
| 144.600 | -107 | -111 | -141 | -153 |
| 146.000 | -104 | -109 | -139 / -140 | -153 / -154 |
| 148.000 | -104 | -108 | -138 / -140 | -153 |
| 152.000 | -110 | -114 | -134 / -137 | -153 |
| 156.000 | -106 | -110 | -115 | -118 |
| 160.000 | -108 | -108 | -134 / -139 | -154 / -153 |
| 162.550 (NOAA) | -104 | -106 | -129 / -131 | -146 |
| 170.000 | -115 | -117 | -124 | -153 / -152 |
| 440.925 | -85 (the repeater, all rounds) | -132 (2 quiet rounds) | -134 / -136 | -136 / -151 |

Where a cell has two figures they are the two runs'.

- **No hump at 144.390 MHz.** The floor is the same from 144.200 to 144.600 in both runs, to the
  dB: no narrow source near the APRS channel (a digipeater's, a neighbour's).
- **The floor moved with the hour.** The VHF path read 2 to 5 dB lower at 03:14Z than at 01:37Z
  from 143 to 170 MHz (136, 140 and 160 MHz did not move), while the chip's floor with neither path on held at -153 to -154.
  Run 1 (17:32Z the day before) had -111 on 144.390. A third run at 03:32Z read the same as
  the second to a dB at every frequency (144.200 to 144.600 all -111 again): the floor fell
  some time between 01:37 and 03:14Z and then held. An amplifier's own noise does not move
  4 dB in an hour and a half; noise from outside does. Two runs are too few to say more than
  that this leans towards the antenna's side.
- **Its shape follows the front end.** The floor is highest at 146 to 148 MHz and falls off
  towards 136 and 170 MHz, which is the VHF path's passband. Both an amplifier's noise and
  a broad noise picked up would show it, so the shape says nothing either way.
- 143.000 and 156.000 MHz read the same with the path off (-131 and -118, the noise meter
  low): signals made inside the K6, not on the air.
- 440.925 MHz is a poor reference in this pair: the repeater was keyed through the first run
  and three rounds of the second. Its two quiet rounds read -132, as in run 1.

The reports are on hamlet under `~/blowfang-work/runs/dmr-N-20261112/` (`fesw-V-before.txt`,
`fesw-V-after.txt`).

## Next

- Run `fesw_vhf` at other hours (morning, midday, a weekend) and lay the tables side by side
  with the two below. A floor that keeps moving by several dB, with the chip's own floor
  steady, says picked-up noise.
- Jeff's antenna-off check (shelved) remains the direct answer.
