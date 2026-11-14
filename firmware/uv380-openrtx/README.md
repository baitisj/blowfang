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

## The range image

`BLOWFANG_RANGE=1 sh firmware/uv380-openrtx/build.sh` builds `out/openrtx_mduv3x0_range_wrap.bin`.
It is the PAGER mode with one change: PTT sends the same XL packets as PHY R, the K6's range mode
(docs/k6-range.md), instead of the 1800 baud pager FFSK. PHY R is coded FFSK at 600 and 1200 Hz,
1200 baud, about three times the air time, for the K6 module `rangerx`, which reads it on the
chip's sensitive receiver. The screen says `K6 RNG PTT #` (or `K6 FSK PTT #`, below). The audio takes the same
pre-emphasised voice path, which is what the K6 reads best (bench run 2). In the emulator,
`tests/test_uv380_openrtx_emu.py` sends with the range build and reads every packet back through
the K6 model.

## The MD-UV380/390 PLUS (10 W)

Jeff's radio is an MD-UV390 PLUS 10 W (firmware D219.043). The Plus swaps two pins against
the plain MD-UV380: the LCD backlight is PC6 and the RF PA band select is PD8, not the other way
round. A plain image on a Plus boots with the screen fully dark (OpenRTX runs: its USB serial
port comes up) and drives the backlight PWM into the PA band select line, so never flash a plain
image on a Plus, and never key one. Found on the first flash, 2026-11-13 08:49Z (I2S probe image).

`sh firmware/uv380-openrtx/build.sh mduv3x0p` builds from OpenRTX's `mduv3x0-plus` branch
(`f969eff`: master `20b4088`, which the patch was written against, plus "Add MD-UV3x0 Plus target",
OpenRTX PR #300 and issue #275), target `mduv3x0p`, which defines `PLATFORM_MDUV3x0P` on top of
the MD-UV3x0 target and swaps `PA_SEL_SW` and `LCD_BKLIGHT` in `pinmap.h`. Images:
`out/openrtx_mduv3x0p*` (with `BLOWFANG_I2SPROBE=1` or `BLOWFANG_RANGE=1` as for the plain
radio). CI builds all three. OpenRTX issue #275 notes that OpenGD77 also treats the 10 W radio's
power levels and calibration differently, so TX power on these images may not match its labels:
keep power low and check it on the bench before any on-air use.

## The I2S probe image

`BLOWFANG_I2SPROBE=1 sh firmware/uv380-openrtx/build.sh` builds `out/openrtx_mduv3x0_i2sprobe_wrap.bin`
(CI builds both images). It asks whether the radio's received audio can reach the MCU with no
mod, which is the block for K6 to UV-380. In this image, PAGER mode receives while it's idle, and
`i2sprobe.c` runs a pass every 5 s. Each pass writes its lines to the radio's USB serial port.
PTT and # still work: the probe stands aside for them. On the radio, this image boots straight
into PAGER mode on 440.850 MHz, so a run needs no menus (PTT there sends the pager message: don't
press it unless that's the test).

**DMA buffers.** On this radio OpenRTX's linker script puts `.bss` in the 64 KB CCM RAM, which no
DMA can reach. Every buffer a DMA stream reads or writes (the probe's captures, its PC3 input, and
the PAGER mode's TX and speaker buffers) is in `.bss2`, the 128 KB RAM. The first flash
(2026-11-13) had them in `.bss`: every capture stopped after one halfword (`n=1`), and the PC3
step waited forever for its input stream, which froze the radio. Its census still showed the
C6000 clocking I2S3 by itself with no mod: PA15 (WS) about 8 kHz, PC10 (CK) toggling fast, data
on PC12. Each capture line now also has `f=` (the DMA streams' flags: bit 3 transfer error,
bit 5 complete; the ext stream's << 8) and `sr=` (the SPI's status register).

**Commands over USB serial** (`i2sp_command` in `i2sprobe.h`). Between passes the probe reads
command lines on the same port, so hamlet can try I2S settings with no reflash, each command ending
in an `ok ...` or `err ...` line: `help`, `pass`, `auto 0|1` (the passes every few seconds),
`c6r`/`c6w` (C6000 registers), `rd ADDR [N]` (memory and registers), `wr ADDR VAL` (only the I2S
ports' registers and DMA1 streams 0, 2 and 3: nothing that can key the radio), `pin` (the probe's
pins), `clk on|off`, `cap PORT s|m FMT FS` (one capture), `raw [a|b] [N]` (its samples) and `buf`.
`python3 tools/uv380_i2sprobe.py cmd /dev/ttyACM0 "auto 0" "cap SPI3 s P16 8000" "raw 64"` sends
them and waits for each answer (stop any listener first).

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

## RAM modules (the probe image)

The probe image also takes signed RAM modules over the same USB serial port (`ortx_mod.h`), so new
code reaches the radio in seconds, with no reflash and no bootloader keys. A module is
position-independent Thumb-2 for the M4F, signed with the same key as K6 modules
(`~/.config/sotto/k6-mod.key`), with its own header magic `U3R` (the bare-metal port's table is
`U3M`), and it runs once in the probe's thread against the table in `ortx_mod.h`: say lines,
snprintf, ms, sleep, stop, scratch RAM (DMA-capable), the C6000's registers, raw USB serial in and
out, PC3 captures, the RSSI, and the probe's own command lines (`api->probe("cap SPI3 s S16 8000")`),
so a module can sweep codec settings and capture after each.

    python3 tools/uv380_i2sprobe.py mod /dev/ttyACM0 firmware/uv380-openrtx/mod/c6dump.c

builds, signs and sends a module (`mod begin`, `mod hex` lines, `mod run`), and prints its lines.
`mod info` says where the arena is (16 KB taken from the heap, in the 128 KB RAM; never CCM, which
can neither run code nor take DMA) and whether the image has a key. A module must call
`api->stop()` in any long loop: it ends on PTT, the speaker preview, a mode change, or the Ctrl-C
byte the tool sends after `--run-timeout` seconds.

**Level 2 (11-13): sotto 1200 on the radio.** The image carries the fixed-point sotto 1200
encoder (8 kHz, the sotto submodule, built by `sotto8k/sotto8k_fx_enc.c` and `_fx_lr_enc.c`) beside
the decoder, and PAGER mode keeps a recorded message (up to 30 s, in CCM) that PTT sends and #
plays in place of the stored one until the next recording or a power-off. A module records with
`enc_begin`, `enc_push` (8 kHz audio, from anywhere: USB serial, the codec, later the mic) and
`enc_end`; the encoder runs in a thread of its own (16 KB stack: its deepest calls take about
8 KB, more than a module's), handed 320 samples at a time. Its state (about 24 KB) comes from a static arena in CCM (`sotto8k/sotto8k_enc_arena.h`), not the heap: on 11-14 the heap's 40 KB left after the module arena couldn't take both it and the thread's stack, and the radio reset as usbenc started. `msg` gives the message back,
`preview` presses #, and `tx_request` sends it as a PTT press would, once the module has ended
and only on 440.850 MHz. `mod/usbenc.c` records audio sent over USB
(`tools/uv380_i2sprobe.py inject /dev/ttyACM0 SPEECH.raw --module
firmware/uv380-openrtx/mod/usbenc.c`) and dumps the superframes, to check the radio's encoder
against `sotto_enc -1200`. Sends a module asks for go at 1 W whatever the channel says. `tx_mic(ms)` is the
keyed mic test (Jeff's pick, 11-13): up to 5 s of FM carrying the microphone as FM mode sends it
(MIC_PWR on, the C6000's mic input), at 1 W on 440.850 MHz only, then the Morse ident, while the
module runs on; `mod/mictx.c` captures I2S3 through it with the route left as the firmware sets
it (`rx_start_as_is`), to see whether the mic reaches the MCU while keyed. A level 1 image refuses
level 2 modules (`r=-3`). The image for this
is both builds at once: `BLOWFANG_I2SPROBE=1 BLOWFANG_RANGE=1 sh build.sh mduv3x0p`
(`out/openrtx_mduv3x0p_i2sprobe_range*`).

**RNG or FSK.** In the range image PTT and `tx_request` send RNG (PHY R) at power-on. Macro key 4
(hold MONI, press 4) switches to FSK, the pager's PHY F, the 1800 baud FFSK the K6's VOICE MSG
decodes and plays as it arrives, and back; the macro menu and the main screen show which. Over USB
(or a module's `api->probe()`), `phy fsk` and `phy rng` do the same and answer with the one in use.
The plain build always sends FSK.

**FUNC: hold to record, let go to send (probe image).** In PAGER mode on 440.850 MHz, holding FUNC
(the black side button above PTT) records the mic as sotto 1200 from the C6000's codec input
(I2S3, `pager_rec.c`, gain 5), LED orange, WITHOUT transmitting: only the C6000 runs its analog
transmit with the mic selected (E0 c1 ... 60 80, the half of OpenRTX's startAnalogTx that leaves
the AT1846S in receive and the PA off; `micsilent`, 11-14 15:31Z). Letting go sends the
recording as PTT would (RNG or FSK, then the ident) at the channel's power. Recording stops
after 31 s, or after 1 s with no samples, whatever FUNC does; when the 30 s store fills the LED
blinks red until FUNC is let go. The mic preamp stays on in PAGER mode, since it needs about 6 s
to settle. The TYT cable in the side jack switches the internal mic out, so record with it
unplugged.

**It plays what it hears (probe image).** While PAGER mode sits idle in receive, the FM
receiver's audio comes off the C6000's codec (I2S3 at 8 kHz, `mod/rxaudio.h`'s route) into the
PHY R receiver (`c/common/range_rx.c`, as the `rangerx` module), and `c/common/pager_rx.c` puts
the message back together from the packets, in any order (its 14.5 KB in the encoder's idle arena,
not the heap). A sotto 1200 message that is whole, or
three quarters there and then quiet for 4 s (the gaps filled from neighbouring frames), becomes
the message (# plays it again, PTT sends it) and plays on the speaker at once; the same message
isn't played again for 60 s. It stops for PTT, FUNC, the speaker preview and every USB command,
and starts again after them. With the cable in it prints `heard pkt seq=.. id=.. frames=G/T`,
`heard whole|part ...` and `heard dropped ...` lines (only while the host has written in the last
30 s: a write with no host busy-waits). `listen 0|1` turns it off or on, `listen` says its state
and the preview's. The LED is green while something is coming in: a packet after a strong sync,
or a message part way there (off once it plays or is dropped). Receive only: no RF register.
PHY F (FSK) isn't received.

The first image (8b87511) froze hard after playing a message it heard with the cable out
(11-14 15:44Z, a battery pull). Suspect: the speaker preview stuck in the DAC driver's sync,
which waits with no timeout, and the RTX thread waiting on it at the next PTT. Now the RTX thread
waits at most 3 s for the preview, and `err busy` says which frame the preview is on.

**Bench knobs (probe image).** Over USB, or a module's `api->probe()`: `lvl N` sets the FFSK
peak (1000-32000, default 16000), `fs N` the rate the modulator is told the PWM runs at
(47000-49000, default 48000, which moves the tones and the baud rate together), and `emph 0|1`
turns the line in's pre-emphasis off or on (default on), and `trim N` adds N Hz (-8000 to 8000)
to the transmit frequency; `mark N` and `space N` set PHY F's tones (800-2600 Hz, 0: 1200 and
1800). Each answers `tune lvl=... fs=... emph=... trim=... mark=... space=...`, and `tune` alone
only answers.

**Transmit frequency trim.** Jeff's radio transmitted 3.58 kHz low on 440.850 (11-14 10:40Z,
measured on the IC-7100 in USB). The IC-7100's wide receiver didn't mind, but the K6's chip FSK never
synced on it, in RNG or FSK. So every image now adds 3580 Hz to the transmit frequency it gives the
AT1846S (`blowfang_txTrimHz` in `radio_UV3x0.cpp`, in every mode); `trim N` changes it until
power-off. Receive is left alone. They last until power-off. They're there because the K6 read none of
the UV-380's first FSK sends (11-14), so hamlet can tune the link on air with no reflash.

**tx_tone (level 3).** `api->tx_tone(ms, hz)` keys the radio like `tx_mic`, but with a tone from
the MCU through the line in, the path every send's audio takes (1 W, 440.850 MHz only, then the
ident), while the module runs on. `mod/micduplex_tone.c` switches the C6000's transmit source
meanwhile and reads I2S3, to see whether the radio can send modem audio and hear the mic at once.

**The key.** CI builds images without it. Before flashing, write it in on hamlet with
`python -m modem.k6 keyimage out/openrtx_mduv3x0p_i2sprobe.bin keyed.bin --key-file
~/.config/sotto/k6-mod.key`, then wrap `keyed.bin` for TYT's bootloader with radio_tool, as
OpenRTX's own build does (`radio_tool --wrap -o keyed_wrap.bin -r UV3X0 -s 0x0800C000:keyed.bin`).
The wrapped file is scrambled, so the key can't be written into a `_wrap.bin`. An image with no
key refuses every module (`err mod run refused r=-5`).

Modules so far (`mod/`): `hello` (the loader's own check), `c6dump` (all 256 C6000 configuration
registers), `c6sweep` (flips each bit of the FM-mode and codec registers in turn and captures I2S3
after each: does any one bit put receiver audio there? On the radio, 11-13, no: the flips only
rescaled the idle waveform, and B9 bit 2 put the C6000 to sleep until a power cycle), `c6codec`
(the same for the codec registers E0-E5, with a read-back and an is-it-still-answering check after
every flip, and the first samples of each capture; on the radio, E1 turned out to be a gain on
what I2S3 carries, x2 to x32, and E3 partly a status register), `c6listen` (routes the receive
audio to the C6000 as the probe's pass does, register 26 fd and PD9 high, and captures in rounds for
90 s at two gains, for a tone run: on 11-13 at 14:52Z it heard the IC-7100's 1000 Hz tone cleanly
in P32 frames, so the UV-380 receives with no hardware mod), `rangerx` (PHY R, the range mode,
received from that audio: SPI3 slave P32 by DMA into a ring (mod/rxaudio.h), c/common/range_rx.c
on every other halfword at 8 kHz, a line a packet; on air 11-13 15:08Z and 15:19Z it read none of
72 packets), `rxrec` (that audio to the USB serial as hex lines, with the DMA's measured rate and 64
raw halfwords; `tools/uv380_i2sprobe.py audio LOG OUT.raw` turns the log back into audio: at 15:23Z
it was exactly 8000 a second and range_rx_host read 42 of 42 packets from it), `rrinject` (the
receiver as rangerx builds it, fed audio over USB by `tools/uv380_i2sprobe.py inject`: the
receiver on the MCU against the same audio the host reads). `tests/test_uv380_ortx_mod.py` runs the loader on QEMU's STM32F405
(`make -C radios/uv380 omqemu`).
