# The K6's broadcast FM chip

The K6 has a second receiver: an RDA5807M broadcast FM tuner beside the FD6818, on a bit-banged
I2C bus (SCL PA4, SDA PA12; Aura edf3b98 `src/board/fm.rs` and `src/drivers/rda5807.rs`). Aura's
FM radio app only tunes, seeks and reads its RSSI, with RDS left off. The pager images leave that
app out, but the chip is still there, powered, and idle. (docs/k6-ais.md said the K6 had no
broadcast chip; that was wrong.)

The chip is a closed low-IF receiver with its own DSP. It hands over FM audio, an RSSI (0-127),
station and stereo flags, and RDS blocks; there is no IF or I/Q out. So what it can give the
K6 is a second, independent receiver for a band view (the FD6818 keeps its own frequency) and
RDS, including the clock group.

## What is known, and where from

| Fact | Source |
|---|---|
| Two I2C addresses: 0x10 writes from REG 02H and reads from 0AH in sequence; 0x11 reaches any one register | RDA5807M datasheet v1.1; PU2CLR RDA5807 library (MIT) |
| Chip ID REG 00H = 0x58xx | Rockbox `rda5802.c` detects it this way |
| 50-115 MHz, sensitivity specified at 50 MHz (1.4 uV EMF) | datasheet v1.1, table 4-1 |
| 50-76 MHz: BAND 11 and REG 07H bit 9 = 0 | datasheet footnote; esp-idf-lib |
| "Expert" tuning: BAND 11, 07H bit 9 = 0, FREQ_MODE 1, REG 08H = (f - 50 MHz) / 1 kHz, giving 50-115.535 MHz in 1 kHz steps | OE3HBW, Simple Wideband FM Radio (his experiments; the datasheet says the base is 76 or 87 MHz) |
| The RSSI needs at least 600 ms after a tune to settle | OE3HBW (Rockbox waits 70 ms, but only for STC) |
| RDS is poor or absent below RSSI ~65 | OE3HBW |
| Only REG 00H and 02H-0FH are documented; a Chinese RDA5807SP guide initialises 53 registers | OE3HBW |
| Verbose mode (BLERA / BLERB, "available only in RDS Verbose mode"): REG 56H, reset 0xAEC0, written 0xAEFF through 0x11; BLERC / BLERD in REG 10H bits 15:14 / 13:12 | Zsolt Kovari, comment of 2017-12-20 on hackaday.io project 9009 |
| REG 06H OPEN_MODE: 11 opens writes to the hidden registers, others reads only | datasheet v1.1 |
| LNA_PORT_SEL REG 05H bits 7:6: 00 none, 01 LNAN, 10 LNAP, 11 both (Aura and PU2CLR use LNAP, Rockbox LNAN) | RDA5807FP sheet via PU2CLR (not in the M's v1.1 sheet) |
| Without verbose, nothing flags errors in blocks C and D: only take data that arrives twice | Christoph Tack, hackaday.io page 6584 |

## The probe module

`c/k6/mod/fmprobe.c` (level 2, receive only, about 5.3 KB at -Oz: it needs a voice image's
11,776-byte store, not a lean image's 4 KB) measures on our radio what a SURF-style FM view and a
clock need. The bus runs open-drain on the MCU's pull-ups, so the MCU never drives a line high
against the chip, and the two pins' settings are put back exactly at the end. The chip's audio
stays muted (DMUTE 0, volume 0) and the speaker amplifier is never touched. At the end the chip
is powered down with REG 56H and OPEN_MODE as they were. If nothing answers on the bus, it puts
the pins back and stops at once.

In order: which addresses answer (0x10, 0x11, and 0x60, the TEA5767-compatible one); a dump of
REG 00H-5FH before and after power-up; a sweep of 87.9-107.9 MHz on the 200 kHz raster (quick
reads); the top 8 peaks read again settled; the strongest station's settled RSSI on each LNA
port (it keeps another port if one beats LNAP by 6); the settling curve (STC, then RSSI every
20 ms for 1 s); the station's shape at 50 kHz steps with channel tuning and at 5 kHz steps
with expert tuning (+-200 kHz), which sizes the lens; an expert sweep of 50-115 MHz at 1 MHz;
verbose mode; then RDS on the three strongest and the weakest peak: groups, clean blocks,
group types, PI, the station name (each pair confirmed twice) and every clock group as it
arrives, so the serial log's own time stamp can be compared with it.

The parameter block (`FMPROBE1`) sets the waits, the sweep, the shape and the RDS listens;
`tools/k6_fmprobe.py patch` changes it in CI's `.bin`, and `tools/k6_fmprobe.py report LOG`
turns a serial log back into numbers. A full run with the defaults takes about 3.5 minutes.
`tests/test_k6_fmprobe.py` runs the module on the emulated M0 against a model of the chip on its
bus, bit by bit: the protocol, the pins put back, no contention, the rows, LNA choice, verbose
mode with and without OPEN_MODE, the name confirmed twice when blocks C and D carry no flags,
the clock group, and a key press stopping it cleanly.

## The UV-21 Pro's chip

The UV-21 Pro's stock firmware talks to an I2C device "0x60" on SCL PA4, with SDA on PB6, the pin
the BK4829's 3-wire bus also uses (docs/uv21-characteristics.md). 0x60 is the address an RDA5807
answers in TEA5767-compatible mode, so it is probably the same chip, but nobody has read its ID.
`radios/uv21/mod/fmprobe.c` is the probe cut down for the UV-21's 4 KB cable modules, run with
the bench's `uv21.mod` step while SURF runs (no hands, no flash). It scans every address, reads
16 bytes from each that answers and the ID (REG 00H over 0x11), and only goes on if that is
0x58xx. The bus is open-drain on the pull-ups as on the K6, the BK4829's SCN (PB3) is never
touched and stays high, so the BK4829 ignores the traffic on its SDA, and both pins end as they
were. 4 KB holds half the probe, so it runs twice: `FP_PART=7` (scan, dumps, sweep, peaks, LNA,
settling, the 50 kHz and expert shapes and the expert sweep, about 50 s), then `FP_PART=8`
(sweep, verbose mode, RDS and the clock group). The settings are `-D` numbers (the module's
header lists them); `FP_CLK` picks the chip's reference if 26 MHz turns out wrong.
`tools/k6_fmprobe.py report` reads its log too, and `tests/test_uv21_fmprobe.py` runs both
parts on an emulated M4 against the same chip model on the UV-21's two ports.

## For SURF

Broadcast stations are 200 kHz wide (+-75 kHz deviation plus the subcarriers), so the FD6818
SURF's +-20 kHz lens would sit inside one station. A broadcast lens of about +-100 kHz with outer
bins every 100 kHz fits the raster; the expert shape row says how fine the lens bins can usefully
be (the chip's own filter sets that), and the settling curve sets the quick and settled read
times, which decide how long a sweep takes.
