/* The radio: after TYT's 48 KB bootloader, short of the last 128 KB (settings). See sections.ld.
 * SPDX-License-Identifier: MIT */
MEMORY
{
    flash (rx) : ORIGIN = 0x0800C000, LENGTH = 1M - 48K - 128K
    sram (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
    ccm (rw)   : ORIGIN = 0x10000000, LENGTH = 64K
}

INCLUDE sections.ld
