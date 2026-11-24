# The UV-21 Pro firmware port

This is [bf-5rh-firmware-open](https://github.com/sophiel-meow/bf-5rh-firmware-open) (Rust, GPL-3,
commit e52b2360), the open firmware for the Baofeng UV-5RH, moved onto the UV-21 Pro's board. The
two radios share the MCU (AT32F421), the RF chip family (BK4829), the SPI flash layout and the
K-plug bootloader, so the port is mostly the board layer. Everything in this directory is GPL-3
(LICENSE); the rest of radios/uv21 stays MIT. `UPSTREAM-README.md` is the original README.

Build it with `make -C radios/uv21 port` (cargo and the thumbv7em-none-eabi target, which
`rust-toolchain.toml` names). `tests/test_uv21_port.py` runs it in the emulated AT32F421.

## What ports 1 to 12 change

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
  before) and `swd` (ports 1 to 11).

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
| middle side key | next band: 2 m, 1.25 m, 70 cm, GMRS, marine and weather, air, user |
| 1 / 3 | zoom in / out |
| # | next channel step; a long press moves the CW wake to the listening frequency |
| 4 / 5 | FM, DSB, AM / the band's centre |
| 7 / 9 / 8 | fixed gain down / up / AGC again |
| 6 | the receive band switch's next way (port 12, while its polarity is unknown) |
| * / 0 | monitor (squelch open) / hold (no sweeps, carets frozen); independent |
| A/B | how fast the carets fall |
| VFO/MR | type a custom range |
| MENU | the frequency and level on the serial console |
| EXIT | leave SURF for the VFO screen on the same frequency; A/B there comes back to SURF |

SURF also listens for the CW wake beacon, on 440.850 MHz for now: it looks there every 500 ms
while sweeping and every 1.5 s while parked, and two reads 20 dB over the floor in four looks print
`uv21 wake hz=... dbm=... floor=...` on the console and show WAKE HEARD, then it rests 60 s. A long press of # moves
the wake (in RAM; the SPI flash stays read only). The RSSI scale is the K6's (dBm = raw/2 - 174);
it hasn't been measured on the UV-21.

`tests/test_uv21_surf.py` drives SURF in the emulated radio, with a made-up band of stations, and
`sim/uv21/` puts the same emulated radio in a browser page with a clickable faceplate
(`python3 -m sim.uv21`, see its README).
