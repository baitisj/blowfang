# blowfang on the Baofeng UV-21 Pro

The UV-21 Pro on the bench (board `UV21NRFB-YP07`, firmware V0.07) is a KDH-bootloader radio on
Artery's AT32F421 (Baofeng's "SYN2A"). What it is and what we've measured lives in
[docs/uv21-characteristics.md](../../docs/uv21-characteristics.md). This folder holds what it takes
to get blowfang onto it without losing the stock firmware for good.

Status (2026-11-23): the dumper is on the radio (job uv21-flash-1, two agreeing passes); stock
page 0 is being rebuilt. The bring-up image is built and emulator-tested, not flashed. Every
flash below needs Jeff's typed go, and the runner only takes `kdh.flash` as a local submit on
hamlet.

## Why a dumper, and what it costs

The AT32F421's Flash Access Protection lets only code running from the radio's own flash read that
flash, and no stock image for this board is published. So the only way to back the stock firmware
up is to put a little of our own code in the radio's flash and have it read the rest out.

The bootloader makes that cheap. From our disassembly of the UV-5RM's bootloader (the same family,
board `5RHNRFB-YP07`), and the UV-5RM open firmware's `tools/flash.py`:

- `04` (region 1, N packages) only stores N and sets the write offset to 0. Nothing is erased.
- each `03` package erases its own 1 KB page and writes it. Package numbers must come in order
  from 0, and the bootloader refuses anything from 0xF000 up.
- packages 2 .. N-3 are XOR-scrambled (keys `KDHT` / `RBGI` by package number), the rest go
  plain. A count under 2 is read as 5.
- `45` replies, then resets the MCU. Region 2 (`05`, 0x1FFFE400, Baofeng's own code) is a
  separate command we never send.
- the reply echoes the command and puts 06 (ACK) or an error code (E1, E2, E3, E5) in the next
  byte.
- flash mode: at power-on the bootloader enters update mode when a frame with command 42
  (`AA 42 00 00 00 83 F4 EF`) arrives within about 200 ms, or when a key is held. On the 5RM
  that is both side keys (PB6 and PA15); on the UV-21 Pro PA15 is the power switch's sense line,
  and the stock firmware suggests the key is the one on row PB14 (not yet tried). Otherwise it
  starts the app. `kdh.probe --wait-boot-s` sends command 42 every 50 ms, so a plain power cycle
  is enough: proven on the UV-21 Pro on 2026-11-23 (job uv21-probe-1).

Measured on the UV-21 Pro (2026-11-23, job uv21-flash-1): its bootloader is not the 5RM's byte for
byte (different SHA-256), but it behaves the same way. A one-package update erased only page 0;
pages 1-59 came back intact in two identical passes.

So a one-package update costs exactly page 0 of the 60 KB app (0x08001000-0x080013FF). Every
other page, the bootloader itself and region 2 stay as they are.

[dumper/dumper.c](dumper/dumper.c) is that page: under 1 KB of C that sets the clock to the
internal 8 MHz and streams frames at 115200 baud on the K-plug's serial pins:

- a taste of the SPI flash: wake it (0xAB), its JEDEC ID and its first 256 bytes, in SPI mode 3
  and mode 0;
- the whole MCU flash (bootloader, our page, the 59 stock pages);
- the system area (region 2), the flash size register and unique ID, and the user system data;
- the SPI flash's JEDEC ID, then all of the SPI flash.

At boot it sends only the taste. Each `T` it receives asks for another taste, and each `D` for a
full pass. `kdh.dump` tastes first and asks for full passes only if a chip answered. Taste bytes
are compared on hamlet with the first 64 KB that the CPS read already backed up.

Dumper 1 used the 5RM's pins (CS PA8, power latch PF6) and read the SPI flash as all FF with no
JEDEC ID. The UV-21 Pro's stock firmware puts the SPI flash's CS on PA2 (SPI1 PA5/PA6/PA7, mode
3), the power latch on PA8 and a keypad row on PF6. It also sends B9 (deep power-down) at start.
Dumper 2 uses CS PA2, holds PA8 low so the power switch still turns the radio off, and leaves PF6
alone.

This has been done before on the UV-5RM: amoxu flashed a small dumper through the same bootloader
to read out that bootloader (`reverse_engineering/bf-uv5rm-btldr-dumper` in their repository).
Theirs uses the same serial port (USART1 on PA9/PA10, AF1), the same 8 MHz internal clock and
baud divider (69), and the same link address (0x08001000). It only reads: no flash
register is touched, the SPI flash gets only RELEASE FROM POWER-DOWN (AB), JEDEC ID (9F) and
READ (03), and the RF chip's bus pins (PB3/PB5/PB6) and every GPIOB/GPIOC/GPIOF pin stay as reset
left them. `tests/test_uv21_dumper.py` runs it in an emulated AT32F421 and checks all of
that, plus a byte-exact read-back.

## The steps

| # | What | Runner step | Hands-on |
|---|---|---|---|
| 1 | CPS clone read: codeplug, model and version replies, probes of the rest of the address space | `kplug.read` | none |
| 2-4 | One local job (Jeff's go): bootloader handshake, flash the dumper (one package), capture two agreeing passes (about 3.5 minutes each for a 2 MB SPI flash). A failed step ends the job, so no answer to the handshake means nothing is written | `kdh.probe`, `kdh.flash`, `kdh.dump` | one power cycle while `kdh.probe --wait-boot-s 200` listens |
| 5 | Rebuild page 0 (below) | none | none |
| 6 | Restore stock: flash the rebuilt page 0 alone, then repeat step 1 and compare | `kdh.flash` (local, Jeff's go) | one power cycle |
| 7 | The first blowfang bring-up image (below), then its console | `kdh.flash` (local, Jeff's go), `kdh.console` | one power cycle |

The firmware, unique ID and SPI flash (which holds the owner's channels) go to
`~/blowfang-work/private/uv21/<job id>/` on hamlet, never to the run folder: bench results are
published on the inbox branch. The run folder gets sizes, hashes and whether the passes agree.

From step 3 until step 6 (or a blowfang image) the radio is not a radio: page 0 is the dumper's.
While the dumper runs the screen stays dark. To leave it, turn the radio off. Dumper 1 left the
power latch (PA8) high, so with it the battery has to come off.

## Rebuilding page 0

Page 0 of a KDH app is its vector table plus start-up code. In the UV-5RM's factory images (NRF
V0.13 and BK V0.14, from amoxu/Baofeng-UV-5RM-5RH-RE) page 0 is the same in 967 of 1024 bytes,
and every byte that differs is an address: a vector entry, a literal pointer or a branch offset.
The rest of those two images differs almost everywhere. So page 0 is rebuilt from the 5RM's
template, with each address taken from the dumped pages 1-59:

- vector entries point at the handlers in the dump (each one a function start);
- branch targets land on the same functions the 5RM's page 0 calls (the C library's start-up,
  `SystemInit`, `main`);
- literal pointers match the dump's `.data` / `.bss` layout (its copy table).

The rebuild will be checked by booting it in the emulator with the dumped pages and system area until
`main` runs, before it goes near the radio. Step 6 then proves it on the radio: the stock firmware
boots, and its CPS clone read matches step 1's.

## The bring-up image

[bringup/](bringup/) is the first blowfang code to run as this radio's app: about 5.5 KB of C
(six pages) that reports what the board is, before anything bigger goes on it. Its pins are in
[bringup/board.h](bringup/board.h), from the stock dump: this board is not wired like the
UV-5RM's despite the name (power latch PA8 not PF6, SPI flash CS PA2, RF chip on PB3/PB5/PB6,
LCD control on PB12/PC15/PB8, a 5 x 4 key matrix). A subsystem whose pins aren't in board.h is
compiled out, so an unknown pin is never driven.

It reports, on the K-plug serial line (115200 8N1, text) and on the screen:

- how the bootloader left the clocks, the chip ID, flash size, the supply voltage (internal
  reference) and whether the external crystal starts; it runs on the internal 8 MHz clock;
- the RF chip's ID (register 0) and, on `r`, all 128 registers; the SPI flash's JEDEC ID; the LCD
  controller's ID (a read the stock firmware never does, so it may read all ones);
- each key press as a matrix bit (column x 8 + row, columns PB0 PB4 PB9 PB10 PB11, rows PC13
  PC14 PF6 PB14), and a census of every input pin, so a key nobody mapped still shows up;
- PTT, which is also the serial receive pin: a press reads as a break (`ptt break n=...`);
- the screen: a one-pixel white frame on the outermost pixels (shows the controller's offsets),
  a red square top left (shows a flip or rotation), labelled R G B W bars (red drawn blue means
  BGR), then the IDs, supply, keys, PTT count and seconds since boot.

What it never does: transmit (the RF code has no register write at all, and the transmitter's PA
enables are the RF chip's own GPIOs), write or erase MCU flash, send the SPI flash anything but
JEDEC ID, or touch SWD (PA13/PA14, which the stock firmware takes over for the receive-band
switch), the audio amplifier or the flashlight. `tests/test_uv21_bringup.py` runs it in an
emulated AT32F421 with models of the RF chip, SPI flash, LCD and keys on board.h's pins and checks
all of that.

Power: it holds the latch (PA8) and drops it when the switch sense (PA15) reads off for 200 ms. If
the sense reads off at boot, the polarity in board.h is wrong and it never acts on it (it says
`auto_off=0`), so a wrong guess can't switch the radio off at every boot.

Console commands, one byte each: `?` help, `i` report, `r` RF registers, `l` redraw the screen,
`p` pin census on/off, `O` power off, `R` reset. The runner's `kdh.console` step sends only
`? i r l p` and saves the text to the run folder (it holds no unique ID or memory contents).

Back to flash mode with no hands: the image resets when it hears the bootloader's command 42
frame (`AA 42 ...`), and `kdh.probe --wait-boot-s` keeps sending that frame, so the bootloader
catches the next one. That assumes the radio stays powered through a reset with the switch on
(the latch only matters once the switch is off); if it doesn't, a plain power cycle does the same.

## Recovery

- The bootloader is never written (region 1 starts at 0x08001000), so flash mode stays reachable
  whatever is in the app area.
- After step 4 a full stock restore is the dumped pages 1-59 plus the rebuilt page 0, through the
  same bootloader (60 packages).
- A flash that stops part way is restarted from the handshake while the radio is still in flash
  mode: `04` sets the write offset back to 0.

## The risk we can't test first

The bootloader in this radio may not be the 5RM's. If it erased the whole app on `04` instead of
page by page, step 3 would cost the stock app, not one page. The radio would still take blowfang
or the UV-5RM open firmware, but stock would be gone. Step 2 shows only that the handshake
matches. The dumper reads this radio's own bootloader, so after step 4 we know for sure.

The way to avoid that risk is a read over SWD with pogo pins on the back pads (no solder). That
only works if Baofeng left Flash Access Protection off, which SWD can read from the flash
controller before anything is written.

## Building

    make -C radios/uv21          # build/dumper.bin (fails past 1 KB), build/bringup.bin (past 60 KB)
    python3 -m pytest tests/test_uv21_bringup.py tests/test_uv21_dumper.py tests/test_uv21_kdh.py

clang, lld and llvm-objcopy, as the K6 modules use. The emulator test needs `pip install unicorn`.

## Sources

- UV-5RM open firmware (pin map, `tools/flash.py`): https://github.com/sophiel-meow/bf-5rh-firmware-open
- UV-5RM / 5RH reverse engineering, factory images and the .BF cipher: https://github.com/amoxu/Baofeng-UV-5RM-5RH-RE
- UV-21R V2 board scans (SWD pads): https://github.com/speters/BF-UV21R-inside
