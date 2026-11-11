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
  -102 dBm, against -117 to -123 dBm on 6 m and 70 cm. If that floor falls with the front end
  off, the noise comes in from the antenna side (the antenna or its cable). If it stays, it is
  made behind the switch, in the radio. It does not tell the antenna from the cable; unplugging
  the antenna does that (on Jeff's hands-on list, shelved).
- **True weak-signal tests with no hands**: the DMR repeater on 440.925 MHz reads -83 to
  -87 dBm here, so about 30 dB of loss puts it at the edge run L found (about -115 dBm). The
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

None yet.
