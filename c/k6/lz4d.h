/* lz4d: a tiny LZ4 block decoder for the K6 (Jeff, 2026-11-14: "pack OTA code uploads too, the
   decoder must be tiny"; 2026-11-20: "use it on SPI in general"; docs/k6-lz4.md).

   It reads one LZ4 block (the LZ4 block format, no frame and no size header: sequences of a
   token, literals, a 2-byte offset and a match) from src (n bytes) and writes what it decodes
   to dst, at most cap bytes. Every byte read and written is checked, one at a time, so:

   - it never reads past src + n nor writes past dst + cap, whatever the bytes say (a match
     reaching before dst, or offset 0, is refused too);
   - the bytes it wrote before it stopped are kept, so a caller that wants only the first few
     bytes of a block (a module's name, k6_lib.c kl_info) gives it a small cap and a short src
     and reads them, though it returns -1;
   - with no wild copies it can decode in place: the packed bytes at the top of the buffer and
     dst at its bottom, as long as the write position never passes the read position
     (k6_mod.c km_unpack keeps KM_LZ4_MARGIN bytes between them; modem/lz4.py checks every
     packed stream it makes against that layout).

   Returns the bytes written (the block ended exactly at src + n), or -1.
   SPDX-License-Identifier: MIT */
#ifndef LZ4D_H
#define LZ4D_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

int lz4d(const uint8_t *src, unsigned n, uint8_t *dst, unsigned cap);

#ifdef __cplusplus
}
#endif
#endif
