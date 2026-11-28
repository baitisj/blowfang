/* scrgrab: see scrgrab.h. SPDX-License-Identifier: MIT */
#include "scrgrab.h"
#include "lz4c.c"

static char *scr_hex(char *p, uint32_t v, int digits) {
  while (digits--) *p++ = "0123456789abcdef"[(v >> (4 * digits)) & 15];
  *p = 0;
  return p;
}

/* no division: the K6's M0 has no divide instruction and its modules no libraries */
static char *scr_dec(char *p, uint32_t v) {
  static const uint32_t ten[] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u,
                                 100u, 10u, 1u};
  int lead = 1;
  for (int i = 0; i < 10; i++) {
    char d = '0';
    while (v >= ten[i]) v -= ten[i], d++;
    if (d != '0' || !lead || i == 9) *p++ = d, lead = 0;
  }
  *p = 0;
  return p;
}

static char *scr_str(char *p, const char *s) {
  while (*s) *p++ = *s++;
  *p = 0;
  return p;
}

static uint32_t scr_crc(uint32_t crc, const uint8_t *p, unsigned n) {
  crc = ~crc;
  while (n--) {
    crc ^= *p++;
    for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}

static void scr_flush(scr_t *s) {
  char line[8 + SCR_HEX_LINE];
  unsigned raw = s->fill * s->row_bytes;
  int n;
  const uint8_t *d;
  if (!s->fill) return;
  n = lz4c(s->band, raw, s->pack, raw, s->tab);
  d = n > 0 ? s->pack : s->band;
  char *p = scr_str(line, "scr b ");
  p = scr_dec(p, s->seq++);
  *p++ = ' ';
  p = scr_dec(p, raw);
  *p++ = ' ';
  scr_dec(p, n > 0 ? (uint32_t)n : 0);
  s->say(line);
  if (n <= 0) n = (int)raw;
  for (int i = 0; i < n;) {
    p = scr_str(line, "scr d ");
    for (int k = 0; k < SCR_HEX_LINE / 2 && i < n; k++) p = scr_hex(p, d[i++], 2);
    s->say(line);
  }
  s->fill = 0;
}

void scr_begin(scr_t *s, int w, int h, int fmt, unsigned row_bytes, unsigned band_rows) {
  char line[48];
  char *p = scr_str(line, "scr 1 ");
  s->row_bytes = row_bytes;
  s->band_rows = band_rows;
  s->fill = s->seq = 0;
  s->crc = 0;
  p = scr_dec(p, (uint32_t)w); *p++ = ' ';
  p = scr_dec(p, (uint32_t)h); *p++ = ' ';
  p = scr_dec(p, (uint32_t)fmt); *p++ = ' ';
  p = scr_dec(p, row_bytes); *p++ = ' ';
  scr_dec(p, band_rows);
  s->say(line);
}

void scr_row(scr_t *s, const uint8_t *row) {
  uint8_t *d = s->band + s->fill * s->row_bytes;
  for (unsigned i = 0; i < s->row_bytes; i++) d[i] = row[i];
  s->crc = scr_crc(s->crc, row, s->row_bytes);
  if (++s->fill == s->band_rows) scr_flush(s);
}

void scr_end(scr_t *s) {
  char line[32];
  scr_flush(s);
  char *p = scr_str(line, "scr e ");
  p = scr_hex(p, s->crc, 8);
  *p++ = ' ';
  scr_dec(p, s->seq);
  s->say(line);
}
