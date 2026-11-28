/* lz4c: see lz4c.h. SPDX-License-Identifier: MIT */
#include "lz4c.h"

static uint32_t lz4c_rd32(const uint8_t *p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* a length's extra bytes after its 15 in the token */
static uint8_t *lz4c_len(uint8_t *o, const uint8_t *oe, unsigned v) {
  for (; v >= 255; v -= 255) {
    if (o >= oe) return 0;
    *o++ = 255;
  }
  if (o >= oe) return 0;
  *o++ = (uint8_t)v;
  return o;
}

/* one sequence: the literals src[0..lit), then (if m) a match of m bytes off back */
static uint8_t *lz4c_seq(uint8_t *o, const uint8_t *oe, const uint8_t *lit, unsigned nlit,
                         unsigned off, unsigned m) {
  unsigned ml = m ? m - 4 : 0;
  if (o >= oe) return 0;
  *o++ = (uint8_t)((nlit < 15 ? nlit : 15) << 4 | (ml < 15 ? ml : 15));
  if (nlit >= 15 && !(o = lz4c_len(o, oe, nlit - 15))) return 0;
  if ((unsigned)(oe - o) < nlit) return 0;
  for (unsigned k = 0; k < nlit; k++) *o++ = lit[k];
  if (!m) return o;
  if (oe - o < 2) return 0;
  *o++ = (uint8_t)off;
  *o++ = (uint8_t)(off >> 8);
  if (ml >= 15 && !(o = lz4c_len(o, oe, ml - 15))) return 0;
  return o;
}

int lz4c(const uint8_t *src, unsigned n, uint8_t *dst, unsigned cap, uint16_t *tab) {
  uint8_t *o = dst;
  const uint8_t *oe = dst + cap;
  unsigned i = 0, anchor = 0;
  if (n > 65535u) return -1;
  for (unsigned k = 0; k < LZ4C_TAB; k++) tab[k] = 0xFFFF;
  if (n > 12) {
    unsigned limit = n - 12, mend = n - 5;   /* a match starts before limit and ends by mend */
    while (i < limit) {
      uint32_t v = lz4c_rd32(src + i);
      unsigned h = (v * 2654435761u) >> (32 - LZ4C_HBITS);
      unsigned c = tab[h];
      tab[h] = (uint16_t)i;
      if (c != 0xFFFF && lz4c_rd32(src + c) == v) {
        unsigned m = 4;
        while (i + m < mend && src[c + m] == src[i + m]) m++;
        if (!(o = lz4c_seq(o, oe, src + anchor, i - anchor, i - c, m))) return -1;
        i += m;
        anchor = i;
      } else {
        i++;
      }
    }
  }
  if (!(o = lz4c_seq(o, oe, src + anchor, n - anchor, 0, 0))) return -1;
  return (int)(o - dst);
}
