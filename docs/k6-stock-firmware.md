# What Baofeng's own K6 source tells us

Baofeng published the stock firmware source for the "K6 Plus Open" (the KD32F328 + FD6818 K6)
as a code drop, mirrored at github.com/cnt7/BAOFENG-UV-K6-Firmware (last pushed 2025-04-10).
It is C, copyright 2024 Baofeng, under the Baofeng Public License BFPL-1.0, which allows
study and personal, non-commercial use. **We read it and cite addresses and facts from it. We
copy none of its code into this repository, and we never redistribute Baofeng firmware or
images.** The drop also has the board schematic (BF_K6_Plus_Open_V1.1.pdf) and the SC5260 and
KD32F328 datasheets.

Most of it is already known here: Aura's FD6818 driver is a port of Baofeng's `DevFD6818.c`
(the same register values: AGC table, DTMF coefficients, MDC1200, frequency scan, AF response
shaping, scrambler), and Aura's SPI flash settings map copies Baofeng's `AddrMap_BFK6.h`. The
new findings are below.

## The LCD has no character ROM; the fonts are in the SPI flash

The SC5260 is a 132 x 65 graphics controller (DDRAM only; datasheet section 1). On the K6 it
is wired write-only (4-wire SPI on SPI2: SCL, SDA, CS PB12, A0 PC15, RST PB8; no read line).
The stock firmware keeps **no font in the MCU's flash**. `src/Driver/FlashFont.c` reads every
glyph from the 2 MB SPI flash (an XM25QH16C on SPI1, chip select PA2):

| font | SPI address | bytes a glyph | layout |
|---|---|---|---|
| 8x16 ASCII | 0x04B7C0 | 16 | 8 columns of the top page, then 8 of the bottom page |
| 5x7 ASCII | 0x04BFC0 | 8 (5 used) | 5 columns |
| 6x12 ASCII | 0x076D40 | 12 | 6 + 6 |
| GB2312 16x16 | 0x010000 | 32 | 16 + 16; glyph ((hi-0xB0)*94 + lo-0xA1 + 846) << 5 for hanzi |
| GB2312 12x12 | 0x04CF80 | 24 | 12 + 12; glyph ((hi-0xB0)*94 + lo-0xA1 + 376) * 24 for hanzi |

ASCII glyph = character - 0x20. Each column is one byte, bit 0 the top pixel: the LCD's own
page format, so a glyph can go to the display with no conversion. The source also names a
12x24 digit font at 0x080000, but Jeff's dump holds other data there (16-bit words), not a font.

Jeff's SPI dump taken before Aura's first format (2026-11-03, on hamlet) holds all five fonts,
and they render cleanly. Aura's `first_boot_format` erases only 0x8000, 0x9000, 0xA000,
0xB000, 0xC000, 0xE000, 0xC0000 (the boot picture), 0xD0000 and the SSTV image from 0xD1000,
and blowfang writes only from 0x0F0000 up, so the fonts should still be on the radio. The
`stockfont` module (below) checks that.

**The pager-only images use the 5x7 font (2026-11-10, Jeff: "switching over to the stock
fonts").** `ui::glyph` in the Aura patch draws every 21-column line (VOICE MSG, EXPLORE's
header, module screens, the boot and CPS screens) from the 5x7 glyphs in the SPI flash, one
glyph read (5 bytes) a character, in 6-pixel cells with the glyph's top at FONT_6X10's
baseline less 6, so the caps sit on the rows 6x10's did and the layouts don't move. That takes
embedded-graphics' text renderer and the 720-byte FONT_6X10 out of those images. EXPLORE's
32-column 4x6 rows keep FONT_4X6 (288 B in MCU flash), drawn by the same code; moving it to
the SPI flash would mean writing it there. At power-on the image checks the 5x7 font's CRC-32
(760 bytes at 0x04BFC0, `07acb73c` on the bench radio) and, when VOICE MSG first starts, says
`K6 FONT 5x7 crc=... ok=1` on the serial cable; on any other value the 21-column lines fall back to the 4x6 glyphs in the same
cells, so a radio without the font still shows readable text. In the `banded` image a line
reads its glyphs only in the bands it reaches.

Metrics (Jeff's dump; the CRCs are the same on the radio), row 0 the cell's top:

| font | CRC-32 | rows used | caps | descenders | advance |
|---|---|---|---|---|---|
| 5x7 | 07acb73c | 0-6 (bit 7 never) | 0-6, baseline 6 | none (g j p q y end on row 6) | 6 (no gap in the glyph) |
| 6x12 | aff16628 | 0-11 | 1-8, baseline 8 (digits and Q to 9) | g p q y to row 10 | 6 (column 5 blank) |
| 8x16 | 98f0c02b | 0-15 (15 only ; _) | 1-11, baseline 11 | g j p q to 14, y to 13 | 8 (column 7 blank) |

Lined up with FONT_6X10 at baseline y: 5x7 top = y - 6, 6x12 top = y - 8, 8x16 top = y - 11.
The 5x7's '|' has a gap at row 3.

## The stock SPI flash map

From `FlashDataMap.h`, `AddrMap_BFK6.h` and Jeff's dump:

| range | stock use | now |
|---|---|---|
| 0x000000-0x00FFFF | channels, VFO, settings, DTMF, FM, calibration at 0xF000 | Aura's settings (same layout) |
| 0x010000-0x0BFFFF | fonts (704 KB reserved; data in 0x010000-0x08FFFF) | untouched |
| 0x0C0000 | boot picture, 1 KB (128 x 64, page format) | Aura's boot logo sector |
| 0x0D0000 | voice prompt index: (length, offset) u32 pairs, offsets from 0x0D0000 | Aura's satellites |
| 0x0D0800-... | voice prompt audio | Aura's SSTV image (0xD1000-0xED200), then the repeater table (0x0F0000) and sotto's codebooks (0x100000) |

## Voice prompts: IMA ADPCM, with its tables in the bootloader

The prompts are 4-bit IMA ADPCM at 8 kHz, clamped to 12 bits, played through TIM1 CH4 on PA11:
PWM period 2000 at 96 MHz (48 kHz carrier), repetition counter 5 so DMA1 channel 5 writes
CCR4 every sixth period (8 kHz), from two 1024-sample buffers (`src/Voice/VoiceBroadcast.c`).
The decoder's index table (16 x int32) and step table (the first 59 of IMA's 89 steps, u16)
are read from **0x08001000 and 0x08001040, inside the 8 KB bootloader**. Jeff's bootloader dump
has both, exactly. Nothing else in the bootloader looks reusable: its only text is "UPDATE" at
0x52C. So ADPCM playback would need no tables of its own, but that saves only ~180 B.

## Power and display details

- Stock battery save: RF chip asleep (REG_30 = 0, REG_37 = 0x1D00, ~200 uA by Baofeng's
  comment) for 800 ms, awake 300 ms. The MCU never sleeps. Aura has its own power save.
- SC5260 power save (datasheet 9-2-19): display off (0xAE) then all pixels on (0xA5) stops its
  oscillator, booster and drivers and keeps the display RAM; 0xA4 then 0xAF wakes it. Its
  extension commands also set the duty (1/9 to 1/65) and frame rate.
- There's no 32 kHz crystal (PC14/PC15 are GPIO; PC15 is the LCD's A0), so no low-power RTC.

## The stockfont module

`c/k6/mod/stockfont.c` (API level 6, a RAM module: nothing written anywhere) checks the fonts
on the live radio. The module API's `fl_read` starts at 0x0F0000, so it drives SPI1 itself,
as Aura left it, and sends only READ (0x03) and READ ID (0x9F). It masks interrupts for each
transaction (at most 256 bytes) so nothing else can use the bus mid-read, and gives up
instead of hanging if the bus stops answering. It says:

```
K6 MOD say spi1 cr1=034f jedec=204015
K6 MOD say crc 8x16 ........ 5x7 ........ 6x12 ........
K6 MOD say crc gb16 ........ gb12 ........
K6 MOD say map #########...#.##############....
```

(the CRCs are zlib's CRC-32 of each font: 95 glyphs for ASCII, 0x010000-0x04B7BF for GB16,
0x04CF80-0x076D3F for GB12; in the map '.' is a 64 KB block of all 0xFF). Then it shows two
screens drawn from the stock glyphs, 8 s each, twice (any key ends it). Compare the CRCs with
the same regions of the pre-format dump. Return: 0 fonts present, 1 an ASCII font is blank, -2
SPI1 not set up, -4 the bus stopped answering.

```sh
python tools/k6_upload.py out/mod-stockfont.bin --key-file ~/.config/sotto/k6-mod.key --api-level 6 \
    --telemetry 4 --rounds 1 --run-s 45
```

Tests: `tests/test_k6_stockfont.py` runs it on an emulated M0 against a modelled SPI flash
(commands allowed, CRCs, map, glyph orientation, stack) and draws its screens
(`python3 tests/test_k6_stockfont.py out.png`).
