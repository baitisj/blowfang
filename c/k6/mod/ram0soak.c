/* ram0soak: can the core keep received messages in the bootloader's RAM? (2026-11-19, FuSotto
   playback: docs/k6-modules.md, `loram`). 0x20000000-0x20001027 is the bootloader's own RAM,
   below Aura's region. Once Aura runs, only hal::txdog's vector table (0x20000000-0x200000BF)
   is used there: `loram` saw nothing written in a second of Aura running. This checks it over
   minutes of the radio's ordinary work: the write build (default) fills 0x20000100-0x20000FFF
   with a pattern and says
     r0 w seed=1234abcd n=960
   and the check build (-DRAM0_CHECK) reads it back after whatever ran in between (uploads, a
   receiver module and its DMA, playback, the screen) and says
     r0 c seed=1234abcd bad=0 first=00000000 blk=0000
   bad: words that differ; first: the first such address; blk: the 256-byte blocks with one, bit
   i = block i (0x20000000 + 256 i). Returns bad. API level 1 (say). SPDX-License-Identifier: MIT */
#include "../k6_mod.h"
K6_MODULE_NAME("RAM0 SOAK");

#define LO 0x20000100u
#define HI 0x20001000u
#ifndef SEED
#define SEED 0x1234abcdu
#endif

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hex(char *p, uint32_t v, int digits) {
  const char *x = "0123456789abcdef";
  while (digits--) *p++ = x[(v >> (4 * digits)) & 15];
  return p;
}

static uint32_t pat(uint32_t a) { return (a * 2654435761u) ^ SEED; }

int k6_main(const k6_api_t *api) {
  volatile uint32_t *w = (volatile uint32_t *)(uintptr_t)LO;
  uint32_t i, n = (HI - LO) / 4u;
  char line[64], *p;
#ifdef RAM0_CHECK
  uint32_t bad = 0, first = 0, blk = 0;
  for (i = 0; i < n; i++)
    if (w[i] != pat(LO + 4u * i)) {
      if (!bad++) first = LO + 4u * i;
      blk |= 1u << ((4u * i + (LO - 0x20000000u)) >> 8);
    }
  p = hex(put(line, "r0 c seed="), SEED, 8);
  p = hex(put(p, " bad="), bad, 3);
  p = hex(put(p, " first="), first, 8);
  p = hex(put(p, " blk="), blk, 4);
  *p = 0;
  api->say(line);
  return (int)bad;
#else
  for (i = 0; i < n; i++) w[i] = pat(LO + 4u * i);
  p = hex(put(line, "r0 w seed="), SEED, 8);
  p = hex(put(p, " n="), n, 3);
  *p = 0;
  api->say(line);
  return (int)n;
#endif
}
