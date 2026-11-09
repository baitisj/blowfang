/* pfprobe: can the flash prefetch be turned back on from RAM, by running on HSI for a moment?
   (2026-11-09) hal::txdog's MEM_MODE remap clears FLASH_ACR's PRFTBE (ACR 0x02 against the
   bootloader's 0x12, acrprobe) and at 96 MHz a write won't set it again. Flash code then runs
   slower, and the FD6818 reads between EXPLORE's DMR detector waits outlast its DMA ring
   (xdmrlisten: `DMR diag e=29681 o=0 g=2221`). PR #18 tried the HSI switch inside txdog, from
   flash, at boot, and the K6 hung silently. This does it from SRAM, one stage at a time, so a
   hang names its step (each stage its own upload: pfprobe_hsi 1, pfprobe_pf 2, pfprobe 3):
     1  SYSCLK to HSI and back to the PLL, nothing else
     2  also FLASH_ACR |= PRFTBE while on HSI
     3  also SYSCFG_CFGR1 written again (MEM_MODE as it is) before PRFTBE, as txdog does
   Interrupts are off from the switch to HSI until the PLL is back. Says (0.1 us, hex regs):
     pf s=2 acr 02 12 12 cfgr 0000000a n 3 5
     pf td before=1234 after=1234
   acr: before, on HSI after the stage's writes, after the PLL is back; cfgr: RCC_CFGR before;
   n: polls until SWS showed HSI, then the PLL (100000: timed out); td: the average of 50 of
   td_read's FD6818 pairs (REG_0B + REG_0C) before and after. Receive only: writes no radio
   register. A hang (no lines, no K6 MOD ret) means a power cycle; the image is untouched.
   Level 2 (fd_read). SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#ifndef STAGE
#define STAGE 3
#endif
#include "../k6_mod.h"

#define RCC_CR 0x40021000u
#define RCC_CFGR 0x40021004u
#define RCC_APB2ENR 0x40021018u
#define FLASH_ACR 0x40022000u
#define SYSCFG_CFGR1 0x40010000u
#define TIM14_CNT 0x40002024u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define TIM17EN (1u << 18)
#define PRFTBE (1u << 4)
#define WAIT_MAX 100000u

static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }

typedef struct {
  uint32_t t, c17, c14;
} clk_t;

static uint32_t now(clk_t *c) {
  uint32_t c17 = rd(TIM17_CNT) & 0xFFFF, c14 = rd(TIM14_CNT) & 0xFFFF;
  uint32_t coarse = ((c14 - c->c14) & 0xFFFF) * 9600u, fine = (c17 - c->c17) & 0xFFFF;
  int32_t d = (int32_t)(int16_t)(uint16_t)(fine - coarse);
  c->c17 = c17, c->c14 = c14;
  c->t += coarse + (uint32_t)d;
  return c->t;
}

__attribute__((noinline)) static uint32_t udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {
  char b[10];
  int n = 0;
  do {
    uint32_t q = udiv(v, 10u);
    b[n++] = (char)('0' + v - 10u * q), v = q;
  } while (v);
  while (n) *p++ = b[--n];
  return p;
}

static char *hex(char *p, uint32_t v, int digits) {
  const char *x = "0123456789abcdef";
  while (digits--) *p++ = x[(v >> (4 * digits)) & 15];
  return p;
}

/* the average of 50 td_read pairs, 0.1 us */
static uint32_t td_avg(const k6_api_t *api, clk_t *c) {
  uint32_t i, t0 = now(c), s = 0;
  for (i = 0; i < 50; i++) s += api->fd_read(0x0B), s += api->fd_read(0x0C);
  __asm volatile("" : : "r"(s));
  return udiv((now(c) - t0) * 10u, 96u * 50u);
}

static uint32_t wait_sws(uint32_t sws) {
  uint32_t n = 0;
  while ((rd(RCC_CFGR) & 0xCu) != sws && n < WAIT_MAX) n++;
  return n;
}

int k6_main(const k6_api_t *api) {
  uint32_t apb2 = rd(RCC_APB2ENR), r17[4], i, before, after, acr0, acr_h, acr1, cfgr, n1, n2, m;
  static const uint32_t off[4] = {0x00, 0x28, 0x2C, 0x0C};   /* TIM17 CR1 PSC ARR DIER */
  char line[60], *p;
  clk_t c;
  wr(RCC_APB2ENR, apb2 | TIM17EN);
  for (i = 0; i < 4; i++) r17[i] = rd(TIM17 + off[i]);
  wr(TIM17 + 0x00, 0), wr(TIM17 + 0x0C, 0), wr(TIM17 + 0x28, 0), wr(TIM17 + 0x2C, 0xFFFF);
  wr(TIM17 + 0x14, 1), wr(TIM17 + 0x10, 0), wr(TIM17 + 0x00, 1);
  c.t = 0, c.c17 = rd(TIM17_CNT) & 0xFFFF, c.c14 = rd(TIM14_CNT) & 0xFFFF;
  before = td_avg(api, &c);

  acr0 = rd(FLASH_ACR), cfgr = rd(RCC_CFGR), m = rd(SYSCFG_CFGR1);
  __asm volatile("cpsid i" ::: "memory");
  wr(RCC_CR, rd(RCC_CR) | 1u);                    /* HSION (on since reset; made sure) */
  for (i = 0; i < WAIT_MAX && !(rd(RCC_CR) & 2u); i++) {}
  wr(RCC_CFGR, cfgr & ~3u);                       /* SW = HSI */
  n1 = wait_sws(0);
#if STAGE >= 3
  wr(SYSCFG_CFGR1, m);                            /* MEM_MODE as it is, written again */
#endif
#if STAGE >= 2
  wr(FLASH_ACR, rd(FLASH_ACR) | PRFTBE);
#endif
  acr_h = rd(FLASH_ACR);
  wr(RCC_CFGR, cfgr);                             /* SW as it was */
  n2 = wait_sws((cfgr & 3u) << 2);
  __asm volatile("cpsie i" ::: "memory");
  acr1 = rd(FLASH_ACR);

  c.c17 = rd(TIM17_CNT) & 0xFFFF, c.c14 = rd(TIM14_CNT) & 0xFFFF;
  after = td_avg(api, &c);
  wr(TIM17 + 0x00, 0);
  for (i = 3; i < 4; i--) wr(TIM17 + off[i], r17[i]);
  wr(RCC_APB2ENR, apb2);

  p = put(dec(put(line, "pf s="), STAGE), " acr ");
  p = put(hex(p, acr0, 2), " "), p = put(hex(p, acr_h, 2), " "), p = hex(p, acr1, 2);
  p = put(hex(put(p, " cfgr "), cfgr, 8), " n ");
  p = dec(put(dec(p, n1), " "), n2);
  *p = 0;
  api->say(line);
  p = put(dec(put(line, "pf td before="), before), " after=");
  *dec(p, after) = 0;
  api->say(line);
  return (int)acr1;
}
