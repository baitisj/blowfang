# PAGER mode: OpenRTX on the UV-380 with blowfang

[OpenRTX](https://github.com/OpenRTX/OpenRTX), the open firmware that already runs on the TYT
MD-UV380 / MD-UV390 / Retevis RT3S, with a blowfang **PAGER** mode added. Jeff picked this base
on 2026-11-10 (docs/uv380-port.md, "Approach"): the radio basics (FM, UI, codeplug, RF drivers,
calibration, power control) come from OpenRTX, and blowfang adds its features as a patch, the
way the K6 builds on Aura.

**PAGER mode** (cycle the mode FM -> M17 -> PAGER in OpenRTX's menu): a press of PTT sends the
stored sotto 1200 message as K6 voice-pager packets (FFSK 1200/1800 Hz, 1800 baud, XL), the
format the K6's VOICE MSG receives, then the station ident in Morse (the callsign from OpenRTX's
settings, or AG7EW while it is still N0CALL). One press sends the whole message, about 15 s;
a second press stops it. It transmits only between 430 and 450 MHz and does not receive.

**#** in PAGER mode plays the stored message on the radio's own speaker, decoded on the radio
by the fixed-point sotto 1200 decoder (8 kHz, from the sotto submodule): the first sotto the
UV-380 itself plays, with no hardware mod (the HR_C6000's DAC is the speaker path OpenRTX's
voice prompts use). It runs in its own thread with a 16 KB stack; a press of PTT stops it
before sending, and # again starts it over.

- `uv380-blowfang.patch`: the change to OpenRTX at `20b4088` (2026-11-07), GPL-3.0-or-later like
  OpenRTX. It adds `OpMode_Pager` (`openrtx/src/rtx`), `OPMODE_PAGER`, the mode in the UI and the
  radio driver (`radio_UV3x0.cpp`: FM voice path with the MCU's audio on the line in and
  pre-emphasis), and the blowfang sources to `meson.build`.
- `build.sh` clones OpenRTX at that commit, applies the patch, copies blowfang's portable
  transmitter from `c/common/` (MIT) and `blowfang_message.h`, and builds the radio image or the
  Linux emulator.
- `sotto8k/`: one wrapper per sotto decoder source, to build each with the UV-380's codec
  flags (`-DSOTTO_V36 -DSOTTO_PITCH_THIRD`, sotto VARIANT=v3.7, in `sotto8k.h`). On OpenRTX's
  GCC 9, which lacks the `__smlald` intrinsics, sotto's `fx.h` uses inline assembly for them. `build.sh` copies `sotto/c`'s
  headers and `c/fixed` next to them (`git submodule update --init sotto` first).
- `blowfang_message.h`: the message, 10 s of the Epistle of James (LibriVox, public domain) in
  sotto 1200. `make_message.py` makes it from any `sotto_enc -1200` output.
- CI: `.github/workflows/uv380-openrtx.yml` builds the radio in OpenRTX's devcontainer image
  and the Linux emulator, and keeps the image as an artifact. Then
  `tests/test_uv380_openrtx_emu.py` runs PAGER mode in the emulator and decodes it on the K6
  model.

**The emulator test.** On Linux, `OpMode_Pager` writes its samples (48 kHz, signed 16-bit) to
the file `BLOWFANG_PAGER_OUT` instead of the modulator, and `BLOWFANG_PAGER` set makes the
emulator start in PAGER mode on 440.850 MHz (a hook in `state.c`). The test presses PTT
through the emulator's shell, then decodes the file with `modem.fsk` (the K6 receiver's bench
model): every packet, the whole message, then the Morse ident. Then it presses #, and the
preview (`BLOWFANG_PREVIEW_OUT` on Linux, 8 kHz) must equal sotto's own `sotto_dec -1200` of
the message sample for sample, after the decoder's 2 superframes of delay. Everything else is the code the
radio runs: OpenRTX's state, threads, UI and rtx layers, and the same packet and FFSK code.
Locally: `sh firmware/uv380-openrtx/build.sh linux`, then
`python -m pytest tests/test_uv380_openrtx_emu.py`.

Not yet run on a radio. Before the first on-air test: the deviation (`LEVEL` in
`OpMode_Pager.cpp`) needs setting on the bench against the IC-7100, and the stock firmware,
codeplug and calibration need backing up on hamlet (never committed).

Edit the patch by applying it to a clone at `20b4088`, changing the tree, and
`git add -N` the new files then `git diff > uv380-blowfang.patch`.

## The I2S probe image

`BLOWFANG_I2SPROBE=1 sh firmware/uv380-openrtx/build.sh` builds `out/openrtx_mduv3x0_i2sprobe_wrap.bin`
(CI builds both images). It asks whether the radio's received audio can reach the MCU with no
mod, which is the block for K6 to UV-380. In this image, PAGER mode receives while it's idle, and
`i2sprobe.c` runs a pass every 5 s. Each pass writes its lines to the radio's USB serial port.
PTT and # still work: the probe stands aside for them.

The idea: on this radio the AT1846S's FM audio goes into the HR_C6000 (PD9 high), and the C6000's
codec digitises it for the speaker. That codec has an I2S port, and the stock MD-380 firmware
uses SPI3/I2S3 to its C5000. PA15, PC10, PC11 and PC12 aren't in OpenRTX's pin map for this
radio. A pass:

1. **Census** (passive). Each candidate pin is read with a pull-up, then a pull-down: `F` follows
   the pulls (nothing drives it), `H` or `L` means driven, and `T<n>` means it toggles n times a
   ms. This runs with the codec as OpenRTX sets it (E0 `c9`) and with E0 bit 0 cleared (`c8`),
   which might make the C6000 the I2S master.
2. **Captures.** If the C6000 clocks a port, the MCU listens as a slave and drives nothing. If
   nothing drives the port's clock and word select (both `F`), the MCU clocks them as master and
   listens on SD (PC12) and ext SD (PC11). There are four codec settings, both settings of PD9,
   four frame formats, and 8 and 16 kHz. Each line sums up each channel: nonzero samples,
   changes, mean, RMS, the strongest of 31 bands and its share of the power.
3. **PC3** through OpenRTX's own input stream, as a baseline. With no mod it should hold nothing.

Pins, C6000 registers and DMA streams are put back after every step. Drive risk: the MCU only
drives PC10 and PA15, and only when the census found nothing driving them.

Run it with a signal on the radio's frequency: the IC-7100 sending PSK31 "AG7EW TEST" (1000 Hz
audio, CW ID first), then a pass with no signal. Read the port with
`python3 tools/uv380_i2sprobe.py listen /dev/ttyACM0 probe.log`, then
`python3 tools/uv380_i2sprobe.py report probe.log`. That ranks the captures by how much of their
power sits at the tone. A capture with the tone on and not off is the path.
