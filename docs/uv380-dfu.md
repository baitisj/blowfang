# UV-380: flash mode without holding keys

Until now every UV-380 reflash started with Jeff holding PTT and the side key above it while
switching the radio on. This page is how hamlet gets the running radio into TYT's flash (DFU)
mode by itself, over the USB cable, so a reflash needs no hands. Leaving flash mode after a
write already worked (`radio_tool usb-leave`).

## How

The UV-380's STM32F405 flash (1 MB, one bank):

| Sectors | Addresses | Holds |
|---|---|---|
| 0-2 | 0x08000000-0x0800BFFF | TYT's bootloader. Nothing here ever writes it. |
| 3 | 0x0800C000-0x0800FFFF | 16 KB: the application's vector table and first code |
| 4-11 | 0x08010000-0x080FFFFF | the rest of the application |

TYT's bootloader starts the application only when sector 3 holds a vector table. With sector 3
blank it waits in DFU (USB 0483:df11), the same state the key combination gives. md380tools used
exactly this on the MD-380 (its `reboot_to_bootloader` DFU command: erase the block at
0x0800C000, then run the bootloader).

Checked on Jeff's MD-UV390 PLUS on 11-16 (bootloader read on hamlet only, never shared): after
its startup the bootloader reads PE11 and PE10 (PTT and the side key above it); both low means
DFU. Otherwise it starts the application only if `(word at 0x0800C000 & 0x2FFE0000) ==
0x20000000`, the usual "stack in SRAM" test: no checksum, no signature, no RTC backup register,
backup SRAM or RAM magic. A blank sector 3 reads 0xFFFFFFFF, fails that test, and so means DFU
at every power-on. The chip is at RDP level 1 (OPTCR 0x0FFF55ED), no sector write protected.

So `dfuboot`, a signed RAM module (`firmware/uv380-openrtx/mod/dfuboot.c`), sent over the radio's
USB serial to the image already running:

1. checks TYT's vector table at 0x08000000 (stack in RAM, reset handler inside the bootloader),
   the running image's at 0x0800C000, VTOR, 1 MB of flash, and that sector 3 is not write
   protected; any doubt and it refuses, having written nothing;
2. says a random nonce and waits 10 s for hamlet to answer `erase <nonce>` (or `dry <nonce>`), so
   a module sent by mistake, or input left over from earlier, erases nothing;
3. from RAM, interrupts off, erases sector 3 (one command, `SB_ERASE_CR` in `stm32boot.h`: sector
   3, 32-bit, never a mass erase; compile-time checked) and resets.

The bootloader comes up, finds no application and waits in DFU. hamlet then flashes as usual
(`radio_tool -d 0 -f -i IMAGE_wrap.bin`, then `radio_tool usb-leave`), which rewrites sector 3.

`bootinfo` (`mod/bootinfo.c`) is the read-only check to run first: read protection level and
write protection bits (OPTCR), both vector tables, RCC_CSR reset flags, and a count of telling
words in the bootloader's literal pools (0x0800C000, the usual stack-range masks, RTC backup
registers, backup SRAM), with `ready=yes` when dfuboot's own checks would pass.

## Running it on hamlet

```sh
python3 tools/uv380_dfu.py info  /dev/ttyACM0                # read only
python3 tools/uv380_dfu.py enter /dev/ttyACM0 --dry          # checks + handshake, no erase
python3 tools/uv380_dfu.py enter /dev/ttyACM0                # erase sector 3, reset, wait for df11
python3 tools/uv380_dfu.py flash /dev/ttyACM0 IMAGE_wrap.bin --sha256 HEX \
        --radio-tool ~/blowfang-work/tools/radio_tool/rust/target/release/radio_tool
```

`flash` enters DFU (skipped when the radio is already there), checks the image's sha256 and that
exactly one 0483:df11 device is on the bus, runs radio_tool's flash and usb-leave, then waits for
the serial port and sends `mod info`. The image must be the keyed Plus build (`build.sh
mduv3x0p`, keyed before wrapping); never a plain `mduv3x0` image on Jeff's MD-UV390 PLUS.

The image on the radio needs the module loader (the I2S probe build, `BLOWFANG_I2SPROBE=1`, on the
radio since 11-13). A future image could take `dfu` as a plain USB command instead; a module
needs no reflash to get there, so it comes first.

## First run (11-16)

17:02:34Z on Jeff's MD-UV390 PLUS, from hamlet, nobody at the radio: dfuboot armed and erased;
0483:df11 was on the bus 0.8 s later; radio_tool wrote the keyed PR #123 image (385 KiB,
0x0800C000-0x0806C580) in 16 s; usb-leave; the serial port and `mod info` were back 2.6 s later.
`info` afterwards: both vector tables as before, `ready=yes`. About 20 s from command to running
radio.

hamlet has no host clang: `uv380_dfu.py` puts `$K6_ATFE`, or the ATfE toolchain under
`~/blowfang-work/toolchains`, on PATH itself to build the modules.

## Failure cases

- **Refused or cancelled**: nothing written, the radio carries on.
- **Erased, then the flash fails or is interrupted** (cable, power, a wrong image): the radio has
  no application, so it comes up in DFU at every power-on, with no keys, and hamlet runs `flash`
  again. The bootloader is never touched, so this is always recoverable.
- **Erased, and no df11 appears** (not expected: the bootloader's test above was read): switch
  the radio off and on; the usual PTT + top side key at power-on still works too, because the
  bootloader is intact.

## Why not the STM32's own ROM DFU

The F405's system memory (0x1FFF0000) has ST's USB DFU too, and a running image can jump to it.
It was set aside because:

- Jeff's radio is at read protection level 1 (`bootinfo`, 11-16). Under RDP 1 the ROM bootloader will not read or write flash; its only way out is "read
  unprotect", which mass erases the whole chip, TYT's bootloader included: a brick that needs
  the case opened.
- Even at RDP 0 the ROM DFU offers the whole flash, bootloader sectors included, so one wrong
  address in a dfu-util command overwrites TYT's bootloader. TYT's bootloader only offers flash
  from 0x0800C000 up.

## Why this is not radio self-reflash

Jeff turned down a radio that rewrites its own firmware (the K6, receiving an image and writing
it itself). dfuboot is different: the radio never writes firmware. It blanks one sector of its
own application so that TYT's untouched bootloader takes over, and the new image still comes
from hamlet over USB, through that bootloader, exactly as a key-held flash does. The radio's
part is the equivalent of the key press, and the worst it can leave behind is a radio waiting in
flash mode.
