/* lz4c: a small greedy LZ4 block packer for the radios (Jeff, 2026-11-28: screen grabs packed with
   LZ4 "to make transport faster"). It writes the standard LZ4 block format, which c/common/lz4d.c
   and modem/lz4.py decode: one hash table of 2^LZ4C_HBITS 16-bit positions (512 B at 8 bits) that
   the caller passes in, so it needs no statics (RAM modules are -fropi) and no heap. Greedy, one
   candidate a position: much faster and smaller than modem/lz4.py's optimal parse, a little looser.

   Blocks up to 65,535 bytes; the format's end rules hold (the last 5 bytes are literals, the last
   match starts 12 or more bytes before the end). Returns the packed length, or -1 when it would pass
   dst + cap (send the bytes unpacked then). SPDX-License-Identifier: MIT */
#ifndef LZ4C_H
#define LZ4C_H
#include <stdint.h>

#ifndef LZ4C_HBITS
#define LZ4C_HBITS 8
#endif
#define LZ4C_TAB (1u << LZ4C_HBITS)        /* entries in the caller's table */

int lz4c(const uint8_t *src, unsigned n, uint8_t *dst, unsigned cap, uint16_t *tab);

#endif
