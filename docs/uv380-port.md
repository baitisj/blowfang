# Porting blowfang to the UV-380

Jeff got a UV-380 on 2026-11-10 and asked to port the project to it, starting with the K6
receiving sotto sent from the UV-380 in the K6's FFSK packet format, developed on an emulator
while the K6 holds the bench. This is the port study and the plan. The first milestone already
runs on an emulated STM32F405 (see "Status").

Facts here come from the OpenRTX source (`platform/targets/MD-UV3x0`, `platform/drivers`, commit
20b4088, 2026-11-07), the OpenRTX website (`hardware/mduv380.md`, `M17/mduv380_mods.md`),
md380tools and the OpenGD77 source, all read on 2026-11-10. Nothing has run on the real radio yet.

## Which radio

"UV-380" almost always means the **TYT (Tytera) MD-UV380**, a dual-band (136-174 / 400-480 MHz)
DMR + FM handheld. The MD-UV390 (IP67, GPS on some), the MD-UV390 Plus and the Retevis RT3S share
the board. Still to confirm from Jeff's radio: the label (model, FCC ID), GPS or not, and whether
it is a newer "Plus" / 10 W revision (OpenRTX's mod guide shows a slightly different board for
the UV390 Plus).

## Hardware

| Part | What | Notes |
|---|---|---|
| MCU | STM32F405VGT6, Cortex-M4F | 1 MB flash, 128 KB SRAM + 64 KB CCM. Stock firmware runs it at 72 MHz, OpenRTX at 168 MHz. 8 MHz crystal |
| Bootloader | TYT's, first 48 KB of flash | apps start at 0x0800C000; recovery mode = hold PTT and the key above it at power-on. It checks the initial SP with an off-by-one (SP must be inside SRAM) |
| RF | AT1846S "radio on a chip" | bit-banged I2C on PC9/PA8, address 0x5C. FM mod/demod, CTCSS, RSSI |
| Baseband | HR_C6000 DMR chip | soft SPI on PE2-PE5, power-down PE6. 4FSK DMR modem, its own audio codec (mic ADC, speaker DAC), FM line-in |
| Flash | W25Q128, 16 MB | SPI1 (PB3-PB5), CS PD7. Codeplug, contacts, DMR ID database, radio calibration |
| Display | 160 x 128 RGB565 TFT | 8-bit parallel on the FSMC |
| Keys | matrix on the LCD data lines, PTT PE11, rotary channel knob | |
| GPS | optional, USART1 | |

The useful comparison with the K6: 8 times the flash, about 8 times the RAM, a Cortex-M4F with
DSP instructions at 168 MHz instead of a 96 MHz M0. The sotto encoder fits (sotto's own M4 bench:
v3.7 encoder 85-93 MHz worst case, decoder 19-43 MHz), so this radio can **encode** sotto, which the K6
never could.

## Audio paths (the main constraint)

| Path | Stock radio | How |
|---|---|---|
| MCU -> RF (modulator) | **works, no mod** | TIM3 PWM on PC8 into the HR_C6000's line-in, then the FM modulator. OpenRTX sends M17 this way |
| MCU -> speaker | **works, no mod** | the HR_C6000's DAC, fed 8 kHz samples over its control SPI ("OpenMusic" FIFO) |
| Mic -> MCU | **needs a hardware mod** (OpenRTX) | the only analogue mic path into the MCU is the VOX line (PA3), and it goes through a diode, so it carries an envelope, not audio |
| RF (demodulated) -> MCU | **needs a hardware mod** (OpenRTX) | no path; the mod wires the HR_C6000's ADC input to PC3 |

Two likely no-mod routes, both unproven on this radio:

- **Mic through the HR_C6000's codec over I2S.** The stock MD-380 firmware drives SPI3/I2S3 and
  I2S3ext (md380tools register annotations), the AMBE vocoder runs on the STM32 there, and
  OpenGD77 on the GD-77 (same HR_C6000) gets mic samples from the chip over I2S ("Start Vocoder
  Encode, I2S mode"). On the UV-380, PA15, PC10, PC11 and PC12 (the I2S3 pins) are unassigned in
  OpenRTX's pin table. If this works, it is almost surely 8 kHz, because it was built to feed AMBE.
- **Sotto over DMR.** The HR_C6000 is a DMR modem. Sending sotto bits where AMBE bits go uses its
  4FSK modem both ways with no mod. (The K6 can't send DMR, so this is for UV-380 to UV-380 and
  for receiving.)

So for now: UV-380 transmit of anything the MCU makes (FFSK, sotto from a stored message) works on
a stock radio; live sotto from the mic needs the I2S probe or a mod; receiving the K6's FFSK on
the UV-380 needs the RF mod or another route.

## Existing open firmware

- **OpenRTX** (GPL-3): supports the MD-UV380 for FM and M17 (M17 with the two mods above).
  Drivers for everything on the board, a codeplug, a UI, a Linux emulator target. Built with meson
  and arm-none-eabi-gcc on the Miosix kernel.
- **md380tools**: patches for the single-band MD-380/390 only; the MD-UV380 is not supported.
- **OpenGD77**: the old GD-77 code is on GitHub; its MD-UV380 build is binary-only (opengd77.com)
  and re-uses the vendor AMBE codec.
- **Flashing**: TYT's updater, `radio_tool`, or dmr.tools (web). The bootloader lives in the
  first 48 KB and isn't touched by an app flash, so recovery mode is always there. Before the
  first flash: read and keep the codeplug and the calibration (on Jeff's machine only, never in a
  repository; vendor firmware is never redistributed).

## What carries over from blowfang

| K6 piece | On the UV-380 |
|---|---|
| sotto codec (fixed point, `sotto/c/fixed`) | as is; encode now fits too. The "Run sotto 1200 16k codec on UV-380" thread owns it; 8 kHz first, 16 kHz behind the same API |
| Pager packets (`modem/pager.py`, K6 receiver) | ported: `c/common/pager_tx.c` builds the same packets and burst byte for byte |
| FFSK modem (profile F, 1200/1800 Hz, 1800 baud) | ported as `c/common/ffsk_tx.c` (TX). RX waits on an RF -> MCU path |
| Module loader (`c/k6/k6_mod.c`, SipHash-signed PIC modules) | ported: `c/common/mod_core.c` (same image, tag, key and replay rule, magic `U3M`), `radios/uv380/src/uv_api.h` (the UV-380 table), `modem/uv380mod.py` (M4F build and signing); see "RAM modules" |
| EXPLORE, band surf, DMR detect | AT1846S RSSI sweeps instead of FD6818 registers; a DMR decoder can use the HR_C6000 directly |
| CW ID, PSK31 ident | CW ID ported (`c/common/cw_tx.c`, after every burst); PSK31 for carriers still to do |
| Aura (Rust) | does not carry over |

## Approach

Portable code goes in `c/common/` (MIT, no OS, no malloc, tested on the host). The UV-380 tree is
`radios/uv380/`: a small bare-metal board layer (start-up, clocks, GPIO, PWM audio out) with two
back ends, the radio and QEMU. Apps call only `board.h`, so the same app runs on both.

For the full radio (FM receive, UI, codeplug, power and calibration handling) there were two
paths; **Jeff picked A on 2026-11-10** (`firmware/uv380-openrtx/`):

- **A. OpenRTX as the base, blowfang as a patch on it** (like Aura on the K6): `firmware/uv380-openrtx/`
  with a pinned OpenRTX commit, our patch and a build script; CI builds OpenRTX from its GitHub
  source. All the radio basics come from code that already works on the UV-380.
- **B. Our own firmware** grown from `radios/uv380`: more control, fewer lines, but every driver
  (AT1846S, HR_C6000, display, keys, calibration) has to be written and bench-proven first.

Either way `c/common/`, the codec and `radios/uv380`'s QEMU harness stay as they are, so the work
done now isn't lost.

## Emulator

QEMU's `netduinoplus2` machine is an STM32F405, from Ubuntu's `qemu-system-arm` package. It has
no RCC, GPIO or DMA model, so `board_qemu.c` skips the clock set-up and sends audio to a host file
through Arm semihosting. `tests/test_uv380_emu.py` runs the firmware there and decodes its FFSK
with `modem.fsk` (the bench model of the K6's chip receiver). Renode models more (DMA, timers,
scriptable peripherals for the AT1846S and HR_C6000) but needs a download that cloud sessions
can't run yet; it can come later in CI.

## Plan

1. **Done (emulator):** the UV-380 sends sotto messages as K6 pager packets, FFSK 1200/1800 Hz at
   1800 baud, XL. Byte-exact against `modem.pager`; decoded by the K6 receiver model from the
   firmware's own output on an emulated F405.
2. **RF bring-up (needs the bench):** AT1846S on 440.850 MHz at low power, HR_C6000 FM line-in,
   PA control, the radio's own calibration from SPI flash; CW ID AG7EW after every burst. First
   on-air test: the UV-380 sends a stored sotto 1200 message, the K6 pager plays it, the IC-7100
   records for deviation checks. (Path A gets most of this from OpenRTX.)
3. **Live voice:** probe the HR_C6000 I2S mic path (no mod), then sotto 1200 encode from the mic,
   send by FFSK to the K6.
4. **Receive on the UV-380:** sotto over DMR between UV-380s (no mod), or the RF -> MCU mod if
   Jeff wants FFSK receive.
5. **Modules, EXPLORE, UI:** module loader with a UV-380 API table, RSSI-sweep explorer, screens.

## Open questions for Jeff

- The exact model and label (UV380, UV390, Plus, GPS?).
- Are hardware mods OK on the UV-380? (The K6 rule was no mods.) Without them, mic and RF audio
  into the MCU depend on the I2S probe working.
- Ok to back up the stock firmware and codeplug on hamlet before the first flash?

## RAM modules

The K6's signed RAM modules (`docs/k6-modules.md`) run on the UV-380 too, with the K6's packet
layer left out for now (the UV-380 has no receive path to the MCU without a hardware mod; a
module comes in as a file under QEMU, later over USB or the air).

- `c/common/mod_core.[ch]`: the image check and run, ported from `c/k6/k6_mod.c`: same header,
  SipHash-2-4 tag, replay serial and key placeholder (so `python -m modem.k6 keyimage` keys a
  UV-380 image as it keys a K6 one, and the same key file works:
  `make -C radios/uv380 MOD_KEY_FILE=~/.config/sotto/k6-mod.key`). The header's first three
  bytes name the radio's table, `K6M` or `U3M`, and they sit under the tag: a K6 module never
  runs on the UV-380 and the other way round, even under one key. Flags must be 0 (no SPI
  library yet).
- `radios/uv380/src/uv_api.[ch]`: the table, level 1: `say`, `ms`, `delay_us`, `led`, `ptt`,
  `scratch`, `audio_start`/`audio_write`/`audio_stop` (the MCU -> RF PWM path; nothing in the
  table keys the radio). Modules land in a 16 KB SRAM arena (CCM can't run code) and run on the
  8 KB main stack. Unlike the K6's M0, a module may divide and use single-precision float.
- `modem/uv380mod.py`: builds a module with clang for `thumbv7em-none-eabihf -fropi`, refuses
  it unless it links byte-identical at two addresses, signs it:
  `python -m modem.uv380mod radios/uv380/mod/hello.c hello.u3m --key-file ~/.config/sotto/k6-mod.key`.
- `radios/uv380/mod/hello.c` (about 1.1 KB): say lines, the K6 hello's sum of squares, a hardware
  divide, a float sum, a scratch check, 100 ms of 1200 Hz from a float oscillator through the
  audio calls; returns 0x55.
- `tests/test_uv380_mod.py` (10 tests, QEMU netduinoplus2, throwaway key): hello runs and its
  tone comes back; wrong key, a changed byte, the K6 magic, API level 2, the library flag,
  serial 0 and a short image are refused; an unkeyed image refuses everything; a module with a
  writable static doesn't build.
- Not yet: the OpenRTX-based image (`firmware/uv380-openrtx`) doesn't carry the loader;
  `mod_core.c` is plain C99 with no OS calls, so it drops in there as a task.

## Status (2026-11-10)

- `c/common/pager_tx.[ch]`, `c/common/ffsk_tx.[ch]`, host front end `pager_tx_host`;
  `tests/test_uv380_pager.py` (19 tests).
- `c/common/cw_tx.[ch]`: the AG7EW Morse ident, keyed as `modem.k6.cw`; the sender appends it
  to every burst. `tests/test_uv380_cw.py`.
- `radios/uv380/`: start-up, link scripts for the radio (0x0800C000) and QEMU (0x08000000),
  `board_radio.c` (168 MHz clocks, SysTick, TIM3 PWM + TIM7 + DMA1 stream 2 audio out, RF pins
  held off), `board_qemu.c` (semihosting), `main.c` (the FFSK pager sender), `rf.h` (no driver:
  every TX call fails, so no build can transmit).
- `tests/test_uv380_emu.py`: the QEMU build decodes on the K6 model (3 tests).
- CI: `.github/workflows/uv380.yml` (Ubuntu toolchain and QEMU, image kept as an artifact).
- `firmware/uv380-openrtx/`: OpenRTX `20b4088` + `uv380-blowfang.patch`, a PAGER mode (PTT sends
  the stored sotto 1200 message as K6 FFSK pager packets, then the Morse ident; 430-450 MHz only).
  CI (`uv380-openrtx.yml`) builds the radio image in OpenRTX's devcontainer and the Linux
  emulator. Not run on a radio.
