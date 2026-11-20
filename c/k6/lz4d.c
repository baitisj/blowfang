/* lz4d: a tiny LZ4 block decoder (see lz4d.h). SPDX-License-Identifier: MIT */
#include "lz4d.h"

/* a length's extension bytes: add them while they are 255, then the last one */
static const uint8_t *more(const uint8_t *s, const uint8_t *e, unsigned *len) {
  unsigned b;
  do {
    if (s >= e) return 0;
    b = *s++;
    *len += b;
  } while (b == 255);
  return s;
}

int lz4d(const uint8_t *s, unsigned n, uint8_t *d, unsigned cap) {
  const uint8_t *e = s + n, *m;
  uint8_t *o = d, *oe = d + cap;
  unsigned t, len, off;
  for (;;) {
    if (s >= e) return -1;
    t = *s++;
    len = t >> 4;
    if (len == 15 && !(s = more(s, e, &len))) return -1;
    while (len--) {
      if (s >= e || o >= oe) return -1;
      *o++ = *s++;
    }
    if (s == e) return (int)(o - d);          /* the last sequence: literals only */
    if (e - s < 2) return -1;
    off = s[0] | (unsigned)s[1] << 8;
    s += 2;
    if (!off || off > (unsigned)(o - d)) return -1;
    len = t & 15;
    if (len == 15 && !(s = more(s, e, &len))) return -1;
    len += 4;
    for (m = o - off; len--;) {
      if (o >= oe) return -1;
      *o++ = *m++;
    }
  }
}
