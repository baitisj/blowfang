/* kl_host: the K6's SPI module library (k6_lib.c) with the module loader (k6_mod.c) on this
   machine, for tests/test_k6_lib.py. Built with the test key 00 01 .. 0f.
   stdin, one command a line:
     pkt HEX        a whitened module packet into the block (km_packet); when the image checks
                    and asks for the library (KM_F_LIB), kl_install writes it to its slot
     load N         kl_load slot N into the block (armed: whether km_run would run it now)
     page N         kl_page slot N into the block (never armed; code: its first word, little-endian)
     info N         kl_info slot N
     flip ADDR      flip the low bit of one SPI flash byte (a worn or overwritten slot)
     keyed 0|1      while keyed the flash calls refuse to program or erase (as the radio's do)
   stdout: one line a command (pkt: r= armed=, and install= for an install; load: r= armed= code_len=; page: r= armed=
   code=; info: r=
   code_len= entry= serial= level= flags=), and at the end "erases= progs=" and every write that
   broke a rule ("RULE ..."): outside the library, a program across a page, an erase off a sector.
   SPDX-License-Identifier: MIT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "k6_lib.h"

#define NOR_BYTES (2u << 20)
static uint8_t nor[NOR_BYTES];
static uint32_t arena[11776 / 4];
static int keyed;
static unsigned erases, progs;

static int f_read(uint32_t addr, void *buf, uint32_t n) {
  if (addr < KL_BASE || addr + n > KL_END) { printf("RULE read %06x+%u\n", (unsigned)addr, (unsigned)n); return -1; }
  memcpy(buf, nor + addr, n);
  return 0;
}
static int f_prog(uint32_t addr, const void *buf, uint32_t n) {
  const uint8_t *p = buf;
  uint32_t i;
  if (keyed) return -1;
  if (addr < KL_BASE || addr + n > KL_END || !n || (addr % KL_PAGE) + n > KL_PAGE) {
    printf("RULE prog %06x+%u\n", (unsigned)addr, (unsigned)n);
    return -1;
  }
  for (i = 0; i < n; i++) nor[addr + i] &= p[i];   /* NOR: a program only clears bits */
  progs++;
  return 0;
}
static int f_erase(uint32_t addr) {
  if (keyed) return -1;
  if (addr < KL_BASE || addr >= KL_END || addr % KL_SECTOR) { printf("RULE erase %06x\n", (unsigned)addr); return -1; }
  memset(nor + addr, 0xFF, KL_SECTOR);
  erases++;
  return 0;
}
static const kl_flash_t FL = {f_read, f_prog, f_erase};

int main(void) {
  char line[1024];
  uint8_t p[256], *a = (uint8_t *)arena;
  memset(nor, 0xFF, sizeof nor);
  while (fgets(line, sizeof line, stdin)) {
    unsigned n = 0, v = 0;
    if (!strncmp(line, "pkt ", 4)) {
      for (n = 0; n < sizeof p && sscanf(line + 4 + 2 * n, "%2hhx", &p[n]) == 1; n++) {}
      int r = km_packet(p, (int)n, arena, sizeof arena);
      if (r == KM_READY && (a[13] & KM_F_LIB)) printf("r=%d armed=%d install=%d\n", r, km_armed(), kl_install(a, &FL));
      else printf("r=%d armed=%d\n", r, km_armed());
    } else if (sscanf(line, "load %u", &v) == 1) {
      memset(arena, 0xEE, sizeof arena);
      int r = kl_load((int)v, &FL, a, sizeof arena);
      printf("r=%d armed=%d code_len=%u\n", r, km_armed(), km_status()->code_len);
    } else if (sscanf(line, "page %u", &v) == 1) {
      memset(arena, 0xEE, sizeof arena);
      km_reset();
      int r = kl_page((int)v, &FL, a, sizeof arena);
      printf("r=%d armed=%d code=%u\n", r, km_armed(),
             (unsigned)a[KM_HDR] | (unsigned)a[KM_HDR + 1] << 8 | (unsigned)a[KM_HDR + 2] << 16 |
                 (unsigned)a[KM_HDR + 3] << 24);
    } else if (sscanf(line, "info %u", &v) == 1) {
      kl_info_t i;
      int r = kl_info((int)v, &FL, &i);
      if (r) printf("r=%d\n", r);
      else printf("r=0 code_len=%u entry=%u serial=%u level=%u flags=%u\n", i.code_len, i.entry,
                  (unsigned)i.serial, i.level, i.flags);
    } else if (sscanf(line, "flip %x", &v) == 1) {
      nor[v % NOR_BYTES] ^= 1;
      printf("flipped %06x\n", v);
    } else if (sscanf(line, "keyed %u", &v) == 1) {
      keyed = (int)v;
      printf("keyed=%d\n", keyed);
    }
  }
  printf("erases=%u progs=%u\n", erases, progs);
  return 0;
}
