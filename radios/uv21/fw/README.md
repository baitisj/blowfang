# The UV-21 Pro firmware port

This is [bf-5rh-firmware-open](https://github.com/sophiel-meow/bf-5rh-firmware-open) (Rust, GPL-3,
commit e52b2360), the open firmware for the Baofeng UV-5RH, moved onto the UV-21 Pro's board. The
two radios share the MCU (AT32F421), the RF chip family (BK4829), the SPI flash layout and the
K-plug bootloader, so the port is mostly the board layer. Everything in this directory is GPL-3
(LICENSE); the rest of radios/uv21 stays MIT. `UPSTREAM-README.md` is the original README.

Build it with `make -C radios/uv21 port` (cargo and the thumbv7em-none-eabi target, which
`rust-toolchain.toml` names). `tests/test_uv21_port.py` runs it in the emulated AT32F421.

## What ports 1 to 20 change

- Pins (`src/board/`, with a small `Pin` helper in `src/board/pin.rs`): power latch PA8 and
  sense PA15, LCD on SPI2 (CS PB12, RST PB8, DC PC15, backlight PF7), SPI flash CS PA2, RF chip
  PB3/PB5/PB6, amplifier PB2, flashlight PB7, FM chip clock PA4. No LEDs. PA13/PA14 (SWD) are the receive band switch from port 12.
- Keypad: columns PB0, PB4, PB9, PB10, PB11 and rows PC13, PC14, PF6, PB14, with the UV-21's key
  map (`src/device/keypad.rs`). The side keys ground a row with no column driven.
- Clock: the UV-21's crystal doesn't start, so the PLL runs from the internal 8 MHz clock, halved,
  times 15: 60 MHz.
- Receive only: the radio has no antenna yet. `RX_ONLY` in `src/device/radio.rs` makes
  `enter_tx` refuse, and `src/drivers/fd6818b.rs` filters every RF register write so no transmit
  state, PA bias or TX switch ever reaches the chip.
- SPI flash read only: the flash still holds the stock firmware's VFO, settings and channels in
  the stock formats, and the 5RH code would write its own over them (UP or DOWN saves the VFO at
  0x8000). `READ_ONLY` in `src/drivers/norflash.rs` drops every write enable, program and erase
  until the port owns its storage. Added in port 2.
- Hands-free reflash: the USART1 interrupt watches for the bootloader's command-42 frame
  (`AA 42 00 00 00 83 F4 EF`) and resets, so the next image goes on with no key held.
- Console lines on the K-plug serial line (`src/hal/con.rs`): a boot banner with the build, the
  clock and the safety gates, the RF chip ID, `key id=XX` on each key press, and (port 3) an
  `alive s=... rssi_raw=...` line every 10 s, so a logger that opens late still sees the image.
- Screen (port 3): the 128x160 panel upright in portrait (MADCTL 00). The 5RH's layouts size
  themselves from the screen, so they fit as they are, and the 32 rows left under the meter hold
  an info panel in 5x5 subpixel text (`src/ui/font55.rs`): each font column is two of the panel's
  R, G, B stripes, so a character is four pixels wide and a line is six rows. It shows the build,
  the safety gates and the live RSSI.
- Port 4: port 2 went silent on the radio, so the image now prints `blowfang uv21 port start` on
  the bootloader's own serial setup before it touches the clock (an image that hangs before the
  banner still shows it ran), and the power switch must read off for 200 ms straight before the
  radio switches itself off, as the bring-up image does (the sense line has no pull here).
- Port 5: the 5x5 info panel drops its title line.
- Port 6: SURF is the radio's main screen, and the 5RH's transmit-side and extra features are
  gone: CPS programming, PTT, VOX, DTMF and ANI, dual watch, the scanner, the app launcher and the
  overlay loader. The VFO screen stays as a plain receiver: one band, modulation, tone squelch and
  bandwidth, the meter and the info panel.
- Port 7: no boot splash. The 5RH's header (model and firmware name), welcome lines, battery
  screen, logo, boot tune and one-second wait are gone; the radio goes straight into SURF.
- Port 8: SURF labels the top and bottom of its axis with their frequencies in MHz (440.5 and
  438.5, not +1M and -1M), redraws the frequency only when it changes, and takes the level 0.1 s
  after the squelch opens or closes (or the frequency moves) instead of on every read.
- Port 9: no standing "WAKE 440.850" on SURF's bottom line; it stays blank until a wake is heard,
  then shows WAKE HEARD.
- Port 10: SURF's shared core takes flux_radio's SURF changes (tcarwash/flux_radio, MIT): bin
  frequencies are computed when needed, recentre keeps its old reads as 500 Hz offsets (+-16 MHz),
  and a typed custom range is at most 16 MHz wide. About 440 B less SURF state.
- Port 11: MON and HOLD are independent. MON only opens the squelch (sweeps and the carets' fall
  go on); HOLD stops every sweep and freezes the carets, while the middle listens with the squelch
  as usual and the side keys still jump. The title shows MON and HLD each when on.
- Port 12: the receive front end. The stock firmware switches the receive path with PA14 (VHF,
  220) and PA13 (UHF), the SWD pins; ports 1 to 11 never drove them, and receive was weak. They
  also drove the UV-5RH's band switch on the RF chip's GPIO5/GPIO6, which isn't the switch here
  (one of them probably lights the top LED faintly below 300 MHz). The polarity isn't known yet,
  so key 6 in SURF steps through four ways (`src/board/rxpath.rs`), and the console says
  `fe rx path ...`: `hi` (the band's pin high, the default), `lo`, `hi+56` (with GPIO5/6 as
  before) and `swd` (ports 1 to 11). The stock image, read later, drives the band's pin high on
  receive and both low in idle and transmit: `hi` is right, and key 6 stays as a cross-check.
- Port 13: the RF chip gets the stock firmware's own BK4829 init (`STOCK_INIT_*` in
  `src/drivers/fd6818b.rs`, in its order) instead of the UV-5RH's, which differed in about 15
  registers (VCO bias 0x1F, AFC 0x73, 0x1C/0x1D, audio gain 0x48 and others); the UV-5RH's flat
  AF filter push after it is gone, so the stock filters stand. More register trials go in RAM
  modules, not new images.
- Port 14: no VFO. SURF is the only screen (the operator asked for the VFO code to go): the
  VFO and channel screens, the two sides, the menus and lists, key dispatch, the FM broadcast
  radio, the flashlight and the overlay ABI crate are gone, and so are the SPI flash's VFO,
  channel, contact and FM records (`src/app/mod.rs` keeps the radio, keypad, battery and
  backlight timer). EXIT no longer leaves SURF. Narrow bandwidth uses the stock tune's REG_43
  (0x4008). The image is about half the size: 23 KB, was 45.4 KB.
  In its place, SURF tunes like a VFO: a long # opens frequency entry, and the frequency typed
  becomes the middle of a CUSTOM range as wide as the band on screen (16 MHz at most); a long
  press of the middle side key does the same on the frequency in the middle. Moving the CW wake went from long # to long MENU. The custom range
  shows as CUSTOM (was USER).
- Port 15: TUNE and RAM modules. The long # entry is TUNE: a red title, the frequency in large
  digits with a `_` where the next one goes; `*` is the decimal point and # finishes, so `446#`
  is 446.000 MHz. And RAM modules over the cable (below), for register trials without a new
  image.
- Port 16: HOLD is a steady listen (the operator, on the road with port 15: HOLD turned the
  speaker on, and the scan still pulsed). HOLD makes no retunes at all, not even the CW wake's
  looks, and its squelch is the middle's own: the mean of the last 4 reads against the middle's
  noise floor, where port 15 opened on any one read over the band's floor, which the noise
  crossed every few seconds. The wake still listens through HOLD when the middle is its
  frequency. MON is unchanged: the squelch open, the sweeps and the carets' fall as usual.
- Port 17: a faster screen. The LCD's SPI runs at 15 MHz (PCLK / 4, the ST7735S's rated write
  speed; ports 1 to 16 ran 7.5 MHz), and a changed SURF bar repaints only what changed: the part
  it grew or shrank by, the old caret and the new one (was its whole 103-pixel row pair). In the
  emulator SURF now sends about 1 KB a second to the screen, was about 23 KB. Each line of 5x5
  text goes out in one window (was a window per pixel row: 6 window setups a line, now 1). And
  SURF's state buffer is trimmed to what it uses: 2,304 B, was 3,840 B (1,536 B of RAM back;
  the start line `surf ram need= room=` shows the margin).
- Port 18: SURF's history strip (the operator's pick, history kept in RAM). The bars are 48 px
  wide now and coloured by level over the floor, 3 dB a step (navy, blue, green, yellow,
  orange, white); right of them, past a divider at x 74, a 52-column strip carries those colours
  across, one column a finished sweep, newest at the left (about 40 s of history at ~0.8 s a
  sweep). The levels live in RAM, a nibble a bin (1,638 B), and the strip is sent whole in one
  window after each sweep (`px_begin`/`px_put`/`px_end` in the SURF API). When the middle moves
  the stored columns move with it, so a trail stays on its frequency, and rows tuned into view
  show as none yet; a new span or band starts the strip over. HOLD stops it with the sweeps.
  Building SURF with `-DUV21_HIST=0` drops it all (100 px bars in the old colours).
- Port 19: a faster waterfall (the operator: scan the likely frequencies often, the whole band
  every five seconds). After each full sweep SURF picks the bins worth watching (the peaks it
  found, the carets' bins and the middle, 16 at most, loudest first) and sweeps only those, pass
  after pass, until 5 s after the full sweep began (and at least 1 s after it ended); then the
  next full sweep. Each pass updates those bars, and the strip takes a column every 250 ms
  (52 columns: about 13 s, was about 40 s at a column a sweep). Carets, the floor and the
  peaks' memory still come from full sweeps only; a move starts with a full sweep; HOLD stops
  it all as before. In the emulator a watched carrier is read about 35 times a second, a quiet
  bin once in 5 s. `-DUV21_FAST=0` drops it.
- Port 20: `*` steps through four modes, shown in the title (the operator's spec): SQL-W, the
  squelch as usual and the waterfall waiting while it is open (a refresh sweep every 5 s, as
  before); SQL+W, the waterfall going on while the squelch is open; MON-W, the squelch held open
  (the chip's too), the waterfall waiting; MON+W, held open with the waterfall going. With +W and
  the squelch open SURF scans one bin at a time between 40 ms listens on the middle; each skip's
  retune mutes the speaker and the listen opens it again. Fast passes run 3 s after each full
  sweep there, so the waterfall moves while full sweeps crawl, and the strip takes a column
  every 250 ms once any bin was read (was once a pass). HOLD stops the waterfall in every mode.

## RAM modules (port 15)

A module is a small position-independent C program (`../mod/`, MIT) that runs once in the 12 KB
RAM arena (`hal::arena`, shared with SURF, which leaves for the upload and starts again on its last
frequency after), prints lines on the cable's console and returns. It reaches the
radio only through `um_api_t` in `../mod/uv21_mod.h`: RF chip register reads and writes, tune,
the receive front end, a delay, the time and console lines. Its register writes go through the
same receive-only filter as the image's own, so a module can't key the radio. Nothing is kept: the
next upload replaces it, and a power cycle clears it. `src/umod.rs` takes the upload as text
lines on the K-plug serial line (`um b`, `um w` with a CRC-16 per line, `um r` with the CRC-32 of
the whole image) and answers each one.

    python -m modem.uv21mod run /dev/serial/by-id/<the cable> radios/uv21/mod/hello.c
    python -m modem.uv21mod run <port> radios/uv21/mod/regsweep.c -D SW_REG=0x1F \
        -D SW_VALS=0x1657,0xC65A -D SW_HZ=439500000

`regsweep.c` writes each value to one register in turn, measures the signal on SW_HZ and the
noise 100 kHz beside it (REG_67, half-dB steps), prints a line per value and puts the register
back. On hamlet the bench runner's `uv21.mod` step runs one: the module's name, then its
`NAME=number` settings, and from the inbox a module can come from a pushed branch
(`sources_commit`):

    {"do": "uv21.mod", "args": ["regsweep", "SW_REG=0x1F", "SW_VALS=0x1657,0xC65A"]}

## SURF (port 6)

The radio boots into SURF, the band display from `c/common/surf_core.inc` (shared with the K6
and the RT-950). It is C (MIT), in `../surf/uv21_surf.c`. `build.rs` compiles it with clang, and
`src/surf.rs` hands it the RF chip, the keys, the screen and the firmware's housekeeping through
the `uv_api_t` table in `../surf/uv21_surf.h`. The panel is tall, so the band runs top to bottom,
with the higher frequencies at the top: 63 rows of bars, two pixels each, with the listening
frequency in the middle and carets on the peaks.

| Key | In SURF |
| --- | --- |
| UP / DOWN | the listening frequency up / down one channel step (repeats while held) |
| top side key | jump to the next peak above |
| bottom side key | jump to the next peak below |
| middle side key | next band: 2 m, 1.25 m, 70 cm, GMRS, marine and weather, air, CUSTOM; held: CUSTOM centred on the middle's frequency |
| 1 / 3 | zoom in / out |
| # | next channel step; held: TUNE, type a frequency (`*` for the point, DOWN deletes, # or MENU finishes, EXIT leaves), and CUSTOM is centred on it (the VFO, in SURF) |
| 4 / 5 | FM, DSB, AM / the band's centre |
| 7 / 9 / 8 | fixed gain down / up / AGC again |
| 6 | the receive band switch's next way (port 12, while its polarity is unknown) |
| * / 0 | monitor (squelch open) / hold (no retunes, carets frozen); independent |
| A/B | how fast the carets fall |
| VFO/MR | type a custom range |
| MENU | the frequency and level on the serial console; held: the CW wake moves to the listening frequency |
| EXIT | leave a typed range (SURF is the only screen) |

SURF also listens for the CW wake beacon, on 440.850 MHz for now: it looks there every 500 ms
while sweeping and every 1.5 s while parked, and two reads 20 dB over the floor in four looks print
`uv21 wake hz=... dbm=... floor=...` on the console and show WAKE HEARD, then it rests 60 s. A long press of MENU moves
the wake (in RAM; the SPI flash stays read only). The RSSI scale is the K6's (dBm = raw/2 - 174);
it hasn't been measured on the UV-21.

`tests/test_uv21_surf.py` drives SURF in the emulated radio, with a made-up band of stations, and
`sim/uv21/` puts the same emulated radio in a browser page with a clickable faceplate
(`python3 -m sim.uv21`, see its README).
