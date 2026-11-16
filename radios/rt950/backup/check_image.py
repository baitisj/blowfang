#!/usr/bin/env python3
"""check_image.py: proves a built dump-only image (radios/rt950/backup) can't write or transmit.

  check_image.py build/rt950-backup-only.elf build/rt950-backup-only.bin

Fails unless:
  - spi_flash_write_page and spi_flash_erase_4k are each a lone return (2 bytes), and the 32 KB
    and 64 KB erases are too or are gone, so nothing in the image can erase or program the SPI flash;
  - REG_30's transmit value 0xC1FE appears nowhere in the image, as code or data, so no path can
    put either BK4829 into TX;
  - the CPS identity is RT950-BACKUPONLY, which rt950_backup.py --addr 3 insists on.
Needs arm-none-eabi-nm. SPDX-License-Identifier: GPL-3.0-or-later
"""
import subprocess
import sys

NM = "arm-none-eabi-nm"
MUST_BE_EMPTY = (
    "spi_flash_write_page",
    "spi_flash_erase_4k",
)
EMPTY_OR_GONE = (
    "spi_flash_erase_32k",
    "spi_flash_erase_64k",
    "radio_ptt_on",
)
TX_VALUE = (0xC1FE).to_bytes(2, "little")
IDENTITY = b"RT950-BACKUPONLY"


def sizes(elf):
    out = subprocess.run([NM, "-S", elf], check=True, capture_output=True, text=True).stdout
    got = {}
    for line in out.splitlines():
        w = line.split()
        if len(w) == 4 and w[2] in "tT":
            got[w[3]] = int(w[1], 16)
    return got


def check(elf, binfile):
    """-> a list of what's wrong (empty: the image is safe to upload for a backup)"""
    bad = []
    sz = sizes(elf)
    for name in MUST_BE_EMPTY:
        if sz.get(name) != 2:
            bad.append(f"{name} is {sz.get(name)} bytes, not a lone return")
    for name in EMPTY_OR_GONE:
        if name in sz and sz[name] > 2:
            bad.append(f"{name} is {sz[name]} bytes")
    with open(binfile, "rb") as f:
        img = f.read()
    if TX_VALUE in img:
        bad.append(f"REG_30's TX value 0xC1FE is in the image at 0x{img.index(TX_VALUE):X}")
    if IDENTITY not in img:
        bad.append("no RT950-BACKUPONLY identity")
    return bad


def main():
    bad = check(sys.argv[1], sys.argv[2])
    for b in bad:
        print("NOT SAFE:", b)
    if not bad:
        print("dump-only image: no SPI erase or program, no TX value, identity RT950-BACKUPONLY")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
