# The UV-21 Pro firmware port

This is [bf-5rh-firmware-open](https://github.com/sophiel-meow/bf-5rh-firmware-open) (Rust, GPL-3,
commit e52b2360), the open firmware for the Baofeng UV-5RH, moved onto the UV-21 Pro's board. The
two radios share the MCU (AT32F421), the RF chip family (BK4829), the SPI flash layout and the
K-plug bootloader, so the port is mostly the board layer. Everything in this directory is GPL-3
(LICENSE); the rest of radios/uv21 stays MIT. `UPSTREAM-README.md` is the original README.

Build it with `make -C radios/uv21 port` (cargo and the thumbv7em-none-eabi target, which
`rust-toolchain.toml` names). `tests/test_uv21_port.py` runs it in the emulated AT32F421.

## What ports 1 to 3 change

- Pins (`src/board/`, with a small `Pin` helper in `src/board/pin.rs`): power latch PA8 and
  sense PA15, LCD on SPI2 (CS PB12, RST PB8, DC PC15, backlight PF7), SPI flash CS PA2, RF chip
  PB3/PB5/PB6, amplifier PB2, flashlight PB7, FM chip clock PA4. No LEDs. PA13/PA14 stay SWD.
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
  clock and the safety gates, the RF chip ID, and `key id=XX` on each key press.
- Screen (port 3): the 128x160 panel upright in portrait (MADCTL 00). The 5RH's layouts size
  themselves from the screen, so they fit as they are, and the 32 rows left under the meter hold
  an info panel in 5x5 subpixel text (`src/ui/font55.rs`): each font column is two of the panel's
  R, G, B stripes, so a character is four pixels wide and a line is six rows. It shows the build,
  the safety gates and the live RSSI.
