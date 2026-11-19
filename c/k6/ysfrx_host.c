/* ysfrx_host: the K6's YSF sotto receiver (c/k6/dmrsync.h's grid + c/k6/ysfdec.h +
   c/k6/mod/ysfdec.inc) on the host, for tests/test_k6_ysfrx.py. Reads int16 counts (2 * ones - 64,
   little-endian) on stdin and writes ysfrx's lines (Y call, Y d) as housekeeping would, then
   one summary line: n fr= fich= sf=/ok=. argv[1]: cps8 (96 MHz cycles a DMA transfer, Q8;
   default 4476 = 5.49 MS/s). No DMA, no pauses: the decoders' steps run after every count.
   SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#define DS_FRING 256u
#define YX_TRACE 1
#define YX_CSD 1
#include "dmrsync.h"
#include "ysfdec.h"

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *kv(char *p, const char *k, int v) {
  p = put(p, k);
  return p + sprintf(p, "%d", v);
}

#include "mod/ysfdec.inc"

int main(int argc, char **argv) {
  static ds_t d;
  static yx_t x;
  static int8_t f[DS_FRING];
  static uint8_t msg[6000];
  int16_t buf[4096];
  size_t n, i, hk = 0;
  char line[96], *p;
  uint32_t fr = 0, fich = 0, sf = 0, ok = 0;
  ds_init(&d, argc > 1 ? (uint32_t)atoi(argv[1]) : 4476u);
  x.wbase = d.w16;
  d.f = f;
  yx_mem(&x, msg, sizeof msg, 1);
  while ((n = fread(buf, 2, 4096, stdin)) > 0)
    for (i = 0; i < n; i++) {
      ds_count(&d, buf[i]);
      yx_poll(&x, &d);
      yx_work(&x);
      if (++hk == 4096) {
        hk = 0;
        while ((p = yx_house(&x, line)) != 0) {
          *p = 0, puts(line);
          if (line[2] == 'c') fr += x.nfr, fich += x.nfich, sf += x.nsf, ok += x.nsfok;
        }
      }
    }
  while (x.jf || x.jv || YX_JC(&x)) yx_work(&x);
  if (x.on) yx_end(&x);
  while ((p = yx_house(&x, line)) != 0) {
    *p = 0, puts(line);
    if (line[2] == 'c') fr += x.nfr, fich += x.nfich, sf += x.nsf, ok += x.nsfok;
  }
  printf("n fr=%u fich=%u sf=%u/ok=%u\n", fr, fich, sf, ok);
  return 0;
}
