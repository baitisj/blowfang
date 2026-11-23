# Baofeng UV-21 Pro: characteristics and limitations

What the Baofeng UV-21 Pro on the bench can and can't do for this project, with no hardware mod.
**M** = measured by us (date, run), **I** = inferred, **D** = from documentation or public
teardowns. Keep this current: a new finding goes here as well as in its own doc.

Status (2026-11-23): on the bench, on stock firmware, on the cable the K6 used. First CPS read done
(job `uv21-read-1`, 09:14Z). Nothing has been written to it. **No flash without Jeff's OK:** no factory image is known for this board, so any
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
| Ident | answers `PROGRAMBFNORMALU` (CHIRP's `UV21ProV2`) with 06 | M, 11-23 uv21-read-1 |
| F reply (band limits) | `01 0136 0174 0400 0520 0220 0260 0103 5003`: 136-174, 400-520 and 220-260 MHz (UHF to 520, as on the UV-5RM, not the 480 Baofeng lists); the last two words are not understood yet | M, 11-23 uv21-read-1 |
| M reply (model) | `UV21    +L00000` | M, 11-23 uv21-read-1 |
| Read speed | the CHIRP regions plus 498 more 64-byte blocks (all of 0x0000-0xFFFF) in 9 s, no bad blocks | M, 11-23 uv21-read-1 |
| Bench tool | `tools/bench_kplug.py` through the runner's `kplug.read` step: ident and reads only, never a write; the channel list stays on hamlet | (PR #250) |

## The CPS address space (64 KB, `R` reads)

The CPS reads the first 64 KB of the radio's SPI flash, not the MCU's flash: nothing in it looks
like Cortex-M code. Its layout is the UV-5RM's, block for block, as the UV-5RM open firmware's
`src/flash_map.rs` gives it, down to the calibration addresses. Measured 11-23 (uv21-read-1);
blocks not listed read as FF. **Blocks from 0xF000 up come unscrambled**: the radio's XOR applies
below 0xF000 only (`bench_kplug.py` handles it since PR #251).

| Range | 5RM name | What it holds here |
| --- | --- | --- |
| 0x0000-0x7CFF | channels | 1000 x 32-byte channels; kept on hamlet |
| 0x8000-0x873F | VFO info | wear-levelled 64-byte VFO records, each with a 2-byte CRC |
| 0x9000-0x9DFF | radio info | wear-levelled settings records (0x60 bytes) |
| 0xA000-0xA2BF | DTMF | DTMF settings and contacts |
| 0xC000-0xCFFF | FM | a count (999) then 16-byte records of BCD frequency pairs at band edges (136-174, 400-480) and 400.125 MHz + n x 10 MHz: factory test channels, not the owner's |
| 0xD000-0xD03F | RMW scratch | |
| 0xE000-0xE53F | system | wear-levelled 16-byte state records with a CRC (the counter steps down as records are added) |
| 0xF000-0xF27F | calibration | below |

### Factory calibration (0xF000-0xF27F, measured 11-23)

| | Values |
| --- | --- |
| TX power, APC target (FD6818 reg 0x36 high byte): high | UHF 400-520 by 10 MHz: 230 230 230 226 226 230 230 230 230 240 240 240 240; VHF 130-175: 240 flat; 220 band: 200 flat |
| mid | 190 everywhere |
| low | UHF 146 (400-450) then 166 (460-520); VHF 153; 220 band 120 at 200 MHz, then 200 |
| Squelch thresholds, levels 0-9 | open 0, 114..122; close 0, 113..121 |
| Squelch offsets (13 points per band) | UHF 400: 35-39; VHF 136: 10 falling to 4; 200 MHz: 2 rising to 11; UHF 350: 42 falling to 38 |
| Battery thresholds | 125 132 138 146 160 185 |
| Audio (0xF210) | mic modulation 36, CTCSS 60, DCS 50, AF volume wide 38 / narrow 38, xtal adjust index 9 (zero point 8) |
| Band limits (0xF230; the CPS "F" reply is these without the flags) | on: 136-174, 400-520, 220-260; a fourth entry is off and doesn't parse as MHz |

The flat VHF, 220 and mid-power rows look like defaults, with only UHF high and low trimmed. A
350-400 MHz squelch table and the AM-capable layout point to the FD6818 (BK4819 family) and not
the AT1846S of the UV-21R V2 teardown (**I**, strong; the dumped firmware will settle it). For
blowfang this means the UV-5RM open firmware's calibration code reads this radio's calibration as
it is.

## Bootloader and backup

| | | |
| --- | --- | --- |
| Bootloader protocol | KDH frames `AA cmd args len16 data crc16 EF`; one 1 KB package per page, each package erasing only its own page; region 2 a separate command. From the UV-5RM's bootloader, not yet seen on this radio | D (5RM disassembly) |
| Flash Access Protection | the app flash can't be read from outside, so the backup is a one-page dumper written over app page 0 (`radios/uv21/README.md`) | D |
| Bench tools | `tools/uv21_kdh.py` (probe, flash, dump) through the runner's `kdh.probe`, `kdh.dump` and `kdh.flash` (local submit only) steps | (this PR) |

## To measure

- The RF chip, for certain: from the dumped firmware (its register writes) or a bring-up image.
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
