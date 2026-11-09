/* acrprobe: does hal::txdog leave the MCU's flash prefetch off? (2026-11-09)
   txdog images run flash code ~12% slower (remapspeed: 0x00ca against 0x00b4 calls in MEM_MODE
   2 without txdog), and the slowdown stays after going back to MEM_MODE 2 once the remap has
   been on. That starves EXPLORE's DMR detector (30 lost per step, docs/k6-explore-app.md).
   Guess: switching MEM_MODE drops FLASH_ACR's prefetch buffer (PRFTBS, bit 5, reads 0 while
   PRFTBE, bit 4, is still set). This reads FLASH_ACR and SYSCFG_CFGR1, times 20,000 calls of
   api->ticks (code in MCU flash), writes ACR back as it was, times again, then turns PRFTBE off
   and on again and times a third time. Prefetch on is the reset state, so it leaves it on.
   Receive only, touches no radio register. Says (under 60 characters, times in hex 100 us):
     acr 32 cf 02 t=00ca rw=00ca tog=00b4 acr 32
   API level 1 (say, ticks). Returns tog << 16 | t.
   SPDX-License-Identifier: MIT */
#include "../k6_mod.h"

#define FLASH_ACR 0x40022000u
#define SYSCFG_CFGR1 0x40010000u
#define RCC_APB2ENR 0x40021018u
#define N 20000u

static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hex(char *p, uint32_t v, int digits) {
  const char *x = "0123456789abcdef";
  while (digits--) *p++ = x[(v >> (4 * digits)) & 15];
  return p;
}

static uint32_t t_calls(const k6_api_t *api) {
  uint32_t i, t = api->ticks(), s = 0;
  for (i = 0; i < N; i++) s += api->ticks();
  __asm volatile("" : : "r"(s));
  return (api->ticks() - t) & 0xFFFFu;
}

int k6_main(const k6_api_t *api) {
  uint32_t acr = rd(FLASH_ACR), apb2 = rd(RCC_APB2ENR), cfgr1, t, rw, tog, acr2;
  char line[64], *p;
  wr(RCC_APB2ENR, apb2 | 1u);
  cfgr1 = rd(SYSCFG_CFGR1);
  wr(RCC_APB2ENR, apb2);
  t = t_calls(api);
  wr(FLASH_ACR, acr & 0x1Fu);              /* the same latency and PRFTBE, written again */
  rw = t_calls(api);
  __asm volatile("cpsid i" ::: "memory");
  wr(FLASH_ACR, acr & 0x0Fu);              /* prefetch off */
  wr(FLASH_ACR, (acr & 0x0Fu) | 0x10u);    /* and on */
  __asm volatile("cpsie i" ::: "memory");
  tog = t_calls(api);
  acr2 = rd(FLASH_ACR);
  p = hex(put(line, "acr "), acr, 2);
  p = hex(put(p, " "), apb2 & 0xFFu, 2);
  p = hex(put(p, " "), cfgr1 & 0xFFu, 2);
  p = hex(put(p, " t="), t, 4);
  p = hex(put(p, " rw="), rw, 4);
  p = hex(put(p, " tog="), tog, 4);
  p = hex(put(p, " acr "), acr2, 2);
  *p = 0;
  api->say(line);
  return (int)(tog << 16 | t);
}
