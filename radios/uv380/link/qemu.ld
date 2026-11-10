/* QEMU netduinoplus2: boots at 0x08000000. See sections.ld.
 * SPDX-License-Identifier: MIT */
MEMORY
{
    flash (rx) : ORIGIN = 0x08000000, LENGTH = 1M - 128K
    sram (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
    ccm (rw)   : ORIGIN = 0x10000000, LENGTH = 64K
}

INCLUDE sections.ld
