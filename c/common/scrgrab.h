/* scrgrab: one wire format for a radio's screen on its serial console, whatever the radio (Jeff,
   2026-11-28: "add this to a common ABI for all our devices. That would make any number of
   automated tests easy. Consider using LZ4"). The radio hands it pixel rows; it packs them in bands
   with c/common/lz4c.c and prints them as short hex lines; tools/screen_grab.py turns them back into
   a PNG and checks the CRC. Where the rows come from is the radio's business: the UV-21 reads its
   ST7735S back (radios/uv21/mod/scrdump.c), a radio with a framebuffer copies it, the K6 would have
   to draw its screen again into rows (its ST7565 can't be read).

   Lines (each at most 6 + SCR_HEX_LINE characters: the UV-21's say() prints 64):
     scr 1 W H FMT ROWBYTES BANDROWS   start: format 1; FMT 1 = RGB565, big-endian (what an ST77xx
                                       takes), 2 = 1 bit a pixel, rows left to right, MSB first
     scr b SEQ RAW PACKED              a band: RAW bytes of rows, PACKED bytes of LZ4 block after it
                                       (0: the RAW bytes follow as they are)
     scr d HEX                         the band's bytes
     scr e CRC32 BANDS                 end: CRC-32 (zlib's) over every row byte, the band count
   SPDX-License-Identifier: MIT */
#ifndef SCRGRAB_H
#define SCRGRAB_H
#include <stdint.h>
#include "lz4c.h"

#define SCR_RGB565 1
#define SCR_MONO 2
#ifndef SCR_HEX_LINE
#define SCR_HEX_LINE 56                   /* hex characters a "scr d" line */
#endif

typedef struct scr {
  void (*say)(const char *line);          /* one console line */
  uint8_t *band;                          /* band_rows * row_bytes */
  uint8_t *pack;                          /* as big as band: a block that doesn't fit goes raw */
  uint16_t *tab;                          /* LZ4C_TAB entries */
  unsigned row_bytes, band_rows, fill, seq;
  uint32_t crc;
} scr_t;

/* all buffers belong to the caller (a RAM module's arena, say) */
void scr_begin(scr_t *s, int w, int h, int fmt, unsigned row_bytes, unsigned band_rows);
void scr_row(scr_t *s, const uint8_t *row);
void scr_end(scr_t *s);

#endif
