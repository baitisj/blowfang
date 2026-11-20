/* lz4d_host: the K6's LZ4 block decoder (lz4d.c) on this machine, for tests/test_k6_lz4.py.
   stdin, one line a block: CAP HEX (the most bytes to write, then the packed block in hex).
   stdout, a line each: r=<lz4d's result> out=<the bytes it wrote, in hex>. The output buffer
   sits between guard bytes, and a write past cap prints "GUARD".
   SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lz4d.h"

static uint8_t src[70000], buf[70000 + 64];
static char line[150000];

int main(void) {
  while (fgets(line, sizeof line, stdin)) {
    unsigned cap, n = 0, i, pos = 0;
    int used = 0, r, w;
    if (sscanf(line, "%u %n", &cap, &used) != 1 || cap > 70000) continue;
    while (n < sizeof src && sscanf(line + used + pos, "%2hhx", &src[n]) == 1) n++, pos += 2;
    memset(buf, 0xA5, sizeof buf);
    r = lz4d(src, n, buf + 32, cap);
    for (i = 0; i < 32; i++)
      if (buf[i] != 0xA5 || buf[32 + cap + i] != 0xA5) printf("GUARD ");
    w = 0;
    for (i = 0; i < cap; i++)
      if (buf[32 + i] != 0xA5) w = (int)i + 1;
    printf("r=%d out=", r);
    for (i = 0; i < (unsigned)(r >= 0 ? r : w); i++) printf("%02x", buf[32 + i]);
    printf("\n");
  }
  return 0;
}
