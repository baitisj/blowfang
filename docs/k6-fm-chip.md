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

The UV-21 Pro has its own chip on SCL PA4, with SDA on PB6, the pin the BK4829's 3-wire bus also
uses. A static read of the stock image (2026-11-25, facts only; the image stays private) found an
RDA5807-family register map at 7-bit address 0x30 (bytes 0x60 / 0x61): writes are a register
number and 16 bits, reads are a register number, a stop, then a read that runs on (the stock
status read takes 0AH and 0BH). The setup is a soft reset, REG 02H = 0xC061 (CLK_MODE 26 MHz,
fed from the BK4829's reference), then 05H, 07H and registers 12H, 16H, 1BH and 36H-43H, which
the public RDA5807M sheet doesn't list, so it is probably a variant or clone. No power, reset or
clock pin; PB2 (the amplifier) comes on after the tune. The stock code never reads a chip ID.

On our radio (runs ufmp1 and ufmp5) the chip acknowledges 0x10, 0x11, 0x30 and 0x40, but every
register reads 0xFF before that setup, both the I2C way and the Beken BK1080 way
(`radios/uv21/mod/i2cpeek.c`). `radios/uv21/mod/fmprobe.c` is the probe cut down for the
UV-21's 4 KB cable modules, run with the bench's `uv21.mod` step while SURF runs (no hands, no
flash). It scans the bus, sends the stock setup with the audio muted (DMUTE 0, volume 0, PB2
untouched), then reads the ID and status. The bus is open-drain on the pull-ups as on the K6,
the BK4829's SCN (PB3) is never touched and stays high, and both pins end as they were. 4 KB holds
half the probe, so it runs twice: `FP_PART=7` (scan, dumps, sweep, peaks, LNA, settling, the
50 kHz and expert shapes and the expert sweep, about 50 s), then `FP_PART=8` (sweep, verbose
mode, RDS and the clock group). The settings are `-D` numbers (the module's header lists them).
`tools/k6_fmprobe.py report` reads its log too, and `tests/test_uv21_fmprobe.py` runs both
parts on an emulated M4 against the chip model on the UV-21's two ports, dead until set up.

What else we tried on our UV-21 (2026-11-25, runs ufmp6-15), all with every read still 0xFF:
`radios/uv21/mod/fmstock.c` drives the bus exactly as the stock firmware does (push-pull SCL,
SDA an input with its pull-up for the acknowledge and the data, its timing, the stock status
read and setup), with PA3 held high for over an hour (stock drives it high at boot and never
again), and with PA3 dropped for 3 s with both bus pins low, then raised, before the setup.
Listen tests by the operator at the radio (`FT_KEY_S`: the screen says PRESS TOP KEY, PLAY 1,
PLAY 2 and DONE, with the flashlight blinking while it waits) tuned a strong station and unmuted
at volume 15 with PB2 on: with the BK4829's audio left on only its hiss came through; with it
muted (REG_47 = 0x6042, which the stock FM app needs and is the only thing it relies on besides
PB2) and then with the BK4829 off (REG_30 = 0), silence, with a pop at the switch-off that shows
the amplifier path was live. So the chip acknowledges its addresses but neither answers nor
plays. Stock never reads it except in seek, so nothing yet shows the chip works on this unit
under stock firmware either; that check (stock FM mode on this radio) is the open one.

Later the same night: the operator confirmed this radio played broadcast FM on stock firmware
before our work. A trace of the stock image from reset to the FM setup found no clock, power or
pin step our port lacks; the differences are on the BK4829 (the port drives its GPIO0, 1 and 6
low, REG_33 = 0x9000, where stock leaves REG_33 at its reset value 0xFF00, and runs REG_37 at
0x9D1F where stock receives at 0x9F1F). Runs ufmp16-21 set those, then reset the BK4829 and
loaded the whole stock setup before the FM chip's (`FT_BKSTOCK`): reads stayed 0xFF and the
listen tests stayed silent. (Earlier waits over 279 ms in these modules wrapped short, since
`delay_us` loads SysTick's 24-bit reload once; `um_wait_us` in `uv21_mod.h` fixes that, and
ufmp18-21 ran with real waits.)

Solved (2026-11-25): stock firmware, flashed back for the check, played a broadcast station
cleanly on this radio and its seek worked. Port 21 then made three changes from the stock trace,
all at boot: the BK4829's REG_33 left at its reset value 0xFF00 (no longer 0x9000, which drove
GPIO0, 1 and 6 low), GPIO6 never driven low by `set_rf_gpio56(false)`, and PA4 (the FM chip's
SCL) driven low push-pull with PA3 driven high from reset, as stock does. With it the chip
answers: ID 0x5804, and the probe tunes a station with STC, stereo, FM_TRUE and RSSI 55. Which
of the three mattered isn't known yet (one boot change at a time would say).

RDS on this chip never synchronises (runs ufmp24-26): `FP_PART=8` found eight stations at RSSI
57-63 and heard no group on any of them, with the stock hidden registers (`FP_HIDDEN=1`, the
default), without them (`FP_HIDDEN=0`), and with NEW_METHOD set in REG 02H (`FP_NEWM=1`, now the
default, as on the K6). REG 56H holds no verbose switch either (it reads 0x308d and OPEN_MODE
doesn't change that). It is most likely an RDA variant without RDS, so the RDS module below
targets the K6; the decoder it uses is shared, should a way in turn up.

## The RDS module

`c/k6/mod/fmrds.c` (MENU name FM RDS) is what the probe found, made into something to run:
receive only, a screen at API level 6, the radio's clock at level 8. It sweeps the band, hears
each FM_TRUE station for a few seconds (PI, programme type, TP / TA / music flags, the name),
then hears the stations that gave the most clean groups until their clock group (4A) arrives,
catching their radio text meanwhile. The decoding is `c/common/rds.h`, a group decoder with no
division or library calls, so the UV-21's modules can share it: text is taken only when the same
characters arrive twice for a position, and with verbose mode's error levels a block must also
be clean.

The clock: a station sends 4A once a minute, the group starting within 0.1 s of the minute edge
(IEC 62106), so a clock group says UTC at the moment it arrives (the minute plus the group's own
87.6 ms). The probe saw why one station isn't enough: one clean clock group ran 2 minutes fast,
and two errored ones read hour 26. So a clock group must be clean in blocks B-D and pass range
checks (hour, minute, offset, a date from 2023 on), each becomes an offset (UTC less the
module's ms), and stations agreeing within 1.5 s vote, a PI counting once (a translator repeats
its parent). Two or more set the radio's clock, with a bound of half their spread plus 0.5 s;
one alone only reports (flags 2 lets one clean station set it). Once a clock group has given the
minute's phase, `lead_s` can tune each next station just before the next minute instead of
hearing it the whole minute.

`tools/k6_fmrds.py patch` sets the numbers (`FMRDS001`: the waits, the sweep, the listens, the
stations for the clock, the agreement and bound, the flags), and `report` reads the log back: the
stations with their programme types by name (RBDS names where the extended country code says
North America), the text, every clock group against the vote and the log's own time, and the
vote. What the stations send is site-specific: keep reports out of the repository.
`tests/test_k6_fmrds.py` runs the module on the emulated M0 against the probe's chip model,
here with stations that send names, programme types, text, the country code and minute-edge
clocks: a station 2 minutes fast is outvoted, an errored clock group that reads hour 26 is
refused, one station alone doesn't set the clock, and a key stops it cleanly.

## Measured on our K6 (2026-11-25, bench run fmp1b, image d583fb8)

The module came over the Pluto upload on 440.850 and ran 177 s, receive only. Station
frequencies and names are left out here (they place the bench).

| What | Result |
|---|---|
| Bus | 0x10, 0x11 and 0x60 all answer; REG 00H = 0x5804 (RDA5807M). Random access through 0x11 works, so the K6 doesn't need the sequential-write shadow |
| Band sweep | 101 channels (87.9-107.9, 200 kHz) at 150 ms a channel, 15 s; FM_TRUE on 11; the 8 peaks settle at RSSI 57-68 over a floor near 40 |
| Antenna input | settled RSSI on LNA_PORT_SEL 0/1/2/3: 2 / 35 / 68 / 67. LNAP (Aura's) is right; LNAN is 33 units down; both is no better |
| Settling | STC in 13 ms; RSSI at 90 % after 220 ms and final after about 360 ms (OE3HBW's 600 ms is safe but slow). A 150 ms quick read gives about 80 % of the settled value, enough to rank channels |
| Station shape, expert tuning (5 kHz steps, +-200 kHz) | flat to within 3 RSSI units over 185 kHz, centred at +2.5 kHz; 310 kHz wide 10 units down. The 50 MHz expert base is exact (a wrong base would move it by MHz) |
| Station shape, channel tuning (50 kHz steps) | 40 40 48 52 52 50 48 42 40: the same flat top |
| Expert sweep 50-115 MHz, 1 MHz steps | tunes the whole range; 50-76 MHz sits at the floor (26-34), the whole MHz points next to broadcast stations come up 10+ |
| Verbose mode | REG 56H 0xAEC0 doesn't take 0xAEFF until OPEN_MODE (REG 06H = 0x6000) is set, then it does: BLERC/D in REG 10H work |
| RDS, strongest station (65 s) | 742 groups (11.4 a second, the full rate), every one clean in all four blocks; PI and the name; one 4A clock group |
| RDS, others | a station at RSSI 62: 227 groups, 134 fully clean; its two 4A groups were garbage (hour 26, dates decades off) and both carried BLER 1-2 in C or D, so verbose mode is what rejects them. Two stations at 57 and 65 gave no RDS (likely none sent) |
| The clock | the clean 4A said 03:09 UTC, offset -8 h, while the serial log stamped it about 2 minutes earlier (log time is good to about 30 s here): this station's RDS clock runs about 2 min fast. A clock module must take CT only with BLER 0 and should compare stations |

### What it means for SURF

The chip's RSSI is flat across about +-90 kHz of a station and falls off over the next 60 kHz,
so it can't resolve anything inside a station: a lens bin finer than about 50 kHz reads the same
number as its neighbour. A broadcast lens of +-100 kHz (the operator's suggestion) with 50 kHz bins shows a
station's top and both edges; outer bins every 100 or 200 kHz are the band. Reads every 150 ms
(quick) and 250 ms (about 94 %) put a full 101-channel band at 15-25 s; the K6's FD6818 stays on
its own frequency meanwhile.

## For SURF (before the measurement)

Broadcast stations are 200 kHz wide (+-75 kHz deviation plus the subcarriers), so the FD6818
SURF's +-20 kHz lens would sit inside one station. A broadcast lens of about +-100 kHz with outer
bins every 100 kHz fits the raster; the expert shape row says how fine the lens bins can usefully
be (the chip's own filter sets that), and the settling curve sets the quick and settled read
times, which decide how long a sweep takes.
