# blowfang on the Baofeng UV-21 Pro

The UV-21 Pro on the bench (board `UV21NRFB-YP07`, firmware V0.07) is a KDH-bootloader radio on
Artery's AT32F421 (Baofeng's "SYN2A"). What it is and what we've measured lives in
[docs/uv21-characteristics.md](../../docs/uv21-characteristics.md). This folder holds what it takes
to get blowfang onto it without losing the stock firmware for good.

Status (2026-11-23): nothing has been flashed. Every flash below needs Jeff's typed go, and the
runner only takes `kdh.flash` as a local submit on hamlet.

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

So a one-package update costs exactly page 0 of the 60 KB app (0x08001000-0x080013FF). Every
other page, the bootloader itself and region 2 stay as they are.

[dumper/dumper.c](dumper/dumper.c) is that page: 815 bytes of C that sets the clock to the internal
8 MHz, holds the power latch, and streams frames at 115200 baud on the K-plug's serial pins:

- the whole MCU flash (bootloader, our page, the 59 stock pages);
- the system area (region 2), the flash size register and unique ID, and the user system data;
- the SPI flash's JEDEC ID, then all of the SPI flash.

It sends two passes at boot and one more for each `D` it receives. It only reads: no flash
register is touched, the SPI flash gets only JEDEC ID (9F) and READ (03), and the RF chip's bus
pins stay inputs. `tests/test_uv21_dumper.py` runs it in an emulated AT32F421 and checks all of
that, plus a byte-exact read-back.

## The steps

| # | What | Runner step | Hands-on |
|---|---|---|---|
| 1 | CPS clone read: codeplug, model and version replies, probes of the rest of the address space | `kplug.read` | none |
| 2 | Bootloader handshake, nothing written | `kdh.probe` | power on holding the side key (flash mode), then power-cycle normally |
| 3 | Flash the dumper (one package) | `kdh.flash` (local, Jeff's go) | flash mode again |
| 4 | Capture two agreeing passes (about 3.5 minutes each for a 2 MB SPI flash) | `kdh.dump` | none |
| 5 | Rebuild page 0 (below) | none | none |
| 6 | Restore stock: flash the rebuilt page 0 alone, then repeat step 1 and compare | `kdh.flash` (local, Jeff's go) | flash mode |
| 7 | The first blowfang bring-up image | `kdh.flash` (local, Jeff's go) | flash mode |

The firmware, unique ID and SPI flash (which holds the owner's channels) go to
`~/blowfang-work/private/uv21/<job id>/` on hamlet, never to the run folder: bench results are
published on the inbox branch. The run folder gets sizes, hashes and whether the passes agree.

While the dumper runs the screen stays dark. To leave it, turn the radio off. If the power latch
keeps it on, take the battery off.

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

    make -C radios/uv21          # build/dumper.bin (fails if it grows past 1 KB)
    python3 -m pytest tests/test_uv21_dumper.py tests/test_uv21_kdh.py

clang, lld and llvm-objcopy, as the K6 modules use. The emulator test needs `pip install unicorn`.

## Sources

- UV-5RM open firmware (pin map, `tools/flash.py`): https://github.com/sophiel-meow/bf-5rh-firmware-open
- UV-5RM / 5RH reverse engineering, factory images and the .BF cipher: https://github.com/amoxu/Baofeng-UV-5RM-5RH-RE
- UV-21R V2 board scans (SWD pads): https://github.com/speters/BF-UV21R-inside
