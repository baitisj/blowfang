# Baofeng UV-21 Pro: characteristics and limitations

What the Baofeng UV-21 Pro on the bench can and can't do for this project, with no hardware mod.
**M** = measured by us (date, run), **I** = inferred, **D** = from documentation or public
teardowns. Keep this current: a new finding goes here as well as in its own doc.

Status (2026-11-23): on the bench, on stock firmware, on the cable the K6 used. Nothing has been
written to it. **No flash without Jeff's OK:** no factory image is known for this board, so any
flash is one way.

## The radio

| | | |
| --- | --- | --- |
| Board ID | `UV21NRFB-YP07`, version V0.07 (shown by the radio) | M, 11-23 |
| Board family | `NRFB-YP07` is the suffix of the UV-5RM's `5RHNRFB-YP07` board, the FD6818 build of the SYN2A (AT32F421) family. So the RF chip is probably an FD6818 (= BK4819 family, as on the K6) and not the AT1846S of the UV-21R V2 teardown. Unconfirmed until the CPS replies, a calibration read or a board photo say so | I |
| Bands (Baofeng) | TX 136-174, 220-260, 400-480 MHz | D |
| CHIRP | `UV21ProV2` (ident `PROGRAMBFNORMALU`) or `UV21ProGPS` (`PROGRAMCOLORPROU`); V2 lists AM airband receive, which the AT1846S can't do | D |
| MCU (public UV-21R V2 board) | SYN2A-000 = Artery AT32F421C8T7: Cortex-M4 120 MHz, 64 KB flash (4 KB bootloader + 60 KB app), 16 KB RAM, no DAC; SWD pads RST / 3V3 / DIO / CLK / GND on the back (pogo pins, no solder) | D |
| Display | 1.77" 128x160 colour, portrait (the UV-5RM's open firmware drives a 160x128 landscape ST7735) | D |
| Firmware updates | encrypted .BF through the KDH bootloader; cipher published for the SYN2A family; app flash read-protected | D |

## Programming port

| | | |
| --- | --- | --- |
| Cable | the K6's PL2303 K-plug cable on hamlet (found by its by-id name, not by ttyUSB number) | M, 11-23 |
| Protocol | UV-17 Pro family CPS: 115200 8N1, 16-byte ident magic answered by 0x06, then F / M / SEND, then 64-byte `R` reads with a 4-byte header and an XOR scramble (key "CO 7") | D (CHIRP) |
| Bench tool | `tools/bench_kplug.py` through the runner's `kplug.read` step: ident and reads only, never a write; the channel list stays on hamlet | (PR #250) |

## Bootloader and backup

| | | |
| --- | --- | --- |
| Bootloader protocol | KDH frames `AA cmd args len16 data crc16 EF`; one 1 KB package per page, each package erasing only its own page; region 2 a separate command. From the UV-5RM's bootloader, not yet seen on this radio | D (5RM disassembly) |
| Flash Access Protection | the app flash can't be read from outside, so the backup is a one-page dumper written over app page 0 (`radios/uv21/README.md`) | D |
| Bench tools | `tools/uv21_kdh.py` (probe, flash, dump) through the runner's `kdh.probe`, `kdh.dump` and `kdh.flash` (local submit only) steps | (this PR) |

## To measure

- CPS replies: which ident answers, and the F (band limits on the RT-950) and M (model) replies.
- The codeplug's settings regions, and probes of the rest of the 64 KB address space for
  calibration tables (squelch, power, deviation per band), which would name the RF chip family.
- Receive levels and audio path with the Pluto as the far end (70 cm only for anything we send).
- Whether the FD6818's FSK and AF output reach the MCU the same way as on the K6 and the UV-5RM.
- Whether the bootloader answers the 5RM's handshake (`kdh.probe`), and, from the dump, whether it
  is the 5RM's bootloader byte for byte.
- The SPI flash's JEDEC ID and size, and the MCU's flash size register (from the dump).

## Sources

- Board and firmware background: the project's UV-21 memo (2026-11-19) and Baofeng family tree.
- UV-21R V2 board scans: https://github.com/speters/BF-UV21R-inside
- SYN2A family reverse engineering: https://github.com/amoxu/Baofeng-UV-5RM-5RH-RE
- CHIRP driver: https://github.com/kk7ds/chirp/blob/master/chirp/drivers/baofeng_uv17Pro.py
- UV-5RM open firmware (FD6818 / BK4819 builds): https://github.com/sophiel-meow/bf-5rh-firmware-open
