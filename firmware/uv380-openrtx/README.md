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
