# RT-950 Pro backup

How to save everything on an RT-950 Pro before blowfang code goes near it. The plan, its risks
and the open questions are in the Claude Doc "RT-950 Pro backup plan"; this page is the commands.
Nothing here transmits, and nothing here writes the radio's SPI flash.

## What can and can't be saved

| part | where | how we save it |
|---|---|---|
| channels, settings, calibration | SPI flash 0x000000-0x00FFFF | Stage A: the stock firmware's own CPS read, 2-byte addresses |
| the rest of the SPI flash (fonts, splash, Radtel's data, the OTA area at 0x300000) | SPI flash, 4 MB | Stage B: the dump-only image's CPS read, 3-byte addresses |
| bootloader | MCU flash 0x08000000-0x08002FFF | Stage B: CPS addresses 0x800000-0x802FFF in the dump-only image |
| stock firmware | MCU flash from 0x08003000 | can't be read back: Radtel's own .BTF for the same version is the only copy |

Both bootloader paths (the side keys and the SPI OTA marker 0xA55A at 0x300000) stay as they are,
because nothing writes below 0x08003000 or to the SPI flash.

## Tools

- `radios/rt950/tools/rt950_backup.py`: reads only. It has no write command, and its one output
  path refuses CPS write opcodes. Each range is read until two passes hash the same; each pass
  is kept, with `manifest.json` and `SHA256SUMS`.
- `radios/rt950/backup/backup-only.patch`: jaymart1983's firmware at the pinned commit with
  the SPI erase and page program emptied (no Write Enable is ever sent, so the chip itself
  refuses), CPS writes answered NAK, REG_30's TX value removed, BACKUP ONLY on the status bar,
  CPS identity `RT950-BACKUPONLY`, and the MCU flash readable from CPS address 0x800000. Its
  in-app updater stays, so the way back to stock needs no keys if it takes Radtel's .BTF.
- `radios/rt950/backup/check_image.py`: checks the built image (empty erase and program
  routines, no 0xC1FE anywhere, the identity). `make backup-image` runs it, and CI does too.
- `radios/rt950/tools/fake_rt950.py`: the radio's CPS port on a pty, for dry runs.

```sh
make -C radios/rt950 backup-image JM=../jm      # build/rt950-backup-only.BTF, checked
python -m pytest -q tests/test_rt950_backup.py
```

## The connection

Jeff's RT-950 Pro (2026-11-16) has no K-plug: its side accessory port is a 14-pad pogo connector
with a latch slot and a screw insert, and the USB-C port showed no serial device on hamlet. The
base firmware's notes describe a "Kenwood-style 3.5mm jack" on UART4, so that unit may be another
hardware revision. Until the pogo port's cable and pinout are known, Stage A runs over Bluetooth:

- `radios/rt950/tools/rt950_ble_bridge.py scan` finds the radio (the module's ffe0/ff31/ff32/ffe1
  services); `bridge ADDRESS LINK` connects, runs the module's authentication with UART selector
  5 (9600 baud, the module's default and so most likely the stock firmware's rate; if the
  handshake gets no ACK, try `--sel 0`, `--sel 2`, then `--sel 7` and `--sel 6`, since Radtel's app config names baud code 7, 57600), and makes LINK a serial port. Hamlet
  needs BlueZ running and bleak in the venv.
- `rt950_backup.py read LINK OUT --addr 2 --block 32 --timeout 4` then reads as over a cable.
  At 9600 baud a 64 KB pass takes a minute or two.

Stage B can't go over Bluetooth: the bootloader has none, so uploading the dump image and
returning to stock that way needs a wired connection. (The dump image's in-app updater does
listen on Bluetooth, but the first upload has to reach the stock firmware or the bootloader.)

## On the day

The port is the programming cable's, never the K6's ttyUSB2. `PORT` is its `/dev/serial/by-id` path.
`RUN` is `~/blowfang-work/rt950/runs/rt950-backup-20261117`.

```sh
# Stage A, stock firmware, radio on
python3 rt950_backup.py info "$PORT"
python3 rt950_backup.py read "$PORT" "$RUN/stageA" --addr 2 -v
#   if the stock firmware stalls on addresses its CPS never reads, use --cps-regions

# the gate: Radtel's .BTF for the version on the boot screen
python3 jm/tools/firmware_upload.py verify ~/blowfang-work/rt950/stock/<file>.BTF

# Stage B: radio in bootloader update mode (bottom two side keys held at power-on)
python3 jm/tools/firmware_upload.py upload "$PORT" build/rt950-backup-only.BTF --ptt
python3 rt950_backup.py read "$PORT" "$RUN/stageB" --addr 3 -v      # about 15 minutes
# back to stock: the image's in-app updater first, the side keys if it refuses
python3 jm/tools/firmware_upload.py upload "$PORT" ~/blowfang-work/rt950/stock/<file>.BTF
python3 rt950_backup.py info "$PORT"
```

At 115200 baud a 128-byte read is about 12 ms, so the 4 MB pass takes about 6.5 minutes and
two take about 13. Stage A's 64 KB takes a few seconds a pass.

The backups stay on hamlet and on Jeff's own storage. They never go into git (`.gitignore`
refuses `*.BTF` and `rt950-backup-*/`) or project files.
