/* dmashare: does the IF capture's DMA disturb the pager? (2026-11-09)
   Every GP0 receiver (gp0dma, iflive, ifpager, xdmr, aprsclk) reads GPIOB IDR with DMA channel
   1 at priority "very high". On this chip that channel never waits for its timer: once enabled
   it is requested all the time and runs flat out (5.5-5.9 M transfers/s, PR #69). The pager's
   playback is DMA channel 5 (TIM1_UP, priority "high"), one 16-bit sample into TIM1's CCR4 per
   update, 8000 a second (hal/pwm_audio.rs). Today the two never run together (ifp_session
   refuses while anything plays), but a receiver that listens in the background would. Two
   questions:
     1. does channel 1 starve channel 5 (samples lost or late: clicks, or the pager's stalled
        playback check)?
     2. how much does it slow the CPU: code in MCU flash (the pager's decoder), code in RAM
        (the modules), and the FD6818's bit-banged register reads?

   Channel 5 runs exactly as pwm_audio sets it up (TIM1 48 kHz, an update every 6 periods,
   channel 5 circular from a 640-sample ring to TIM1_DMAR) but with the output stage off: CC4E
   and MOE stay clear and PA11 stays an input, so nothing reaches the speaker. TIM17 counts at
   1 MHz (PSC 95) as the clock, with its update DMA request on, as in ifpager.c. For each of
   channel 1 off, at priority 3 (what every receiver uses) and at priority 0 (below channel 5),
   twice over:
     w: 250 ms of channel 5: the transfers (2000 expected), the longest gap between two of them
        (125 us expected) and channel 1's laps of its 1 KB ring (about 1350 at 5.5 MS/s)
     u: the time of 20,000 calls of api->ticks (MCU flash code), 100 FD6818 reads of REG_67
        (api->fd_read, bit-banged from flash) and 100,000 turns of a loop in RAM reading the
        playback ring (us)
   Lines (under 60 characters):
     dmash start ccr5=0000 cr1=0000 room=3700
     w 1 p=3 n5=2000 gap=125 laps=1350
     u 1 p=3 calls=18000 fd=10000 ram=6000
     dmash done n5=2000/2000/2000
   No radio register is written (REG_67 is only read) and nothing transmits. Writes the MCU's
   RCC AHBENR DMAEN, APB2ENR TIM17EN and TIM1EN, TIM17, TIM1 (no output) and DMA channels 1 and
   5, all put back. Returns n5 at priority 3 (first pass) << 16 | n5 with channel 1 off, or -7
   (TIM17 or channel 1 in use), -9 (TIM1 or channel 5 in use: something plays), -8 (scratch
   under 2,312 bytes). Level 2 (scratch). SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 2
#endif
#include "../k6_mod.h"

#define RCC_AHBENR 0x40021014u
#define RCC_APB2ENR 0x40021018u
#define TIM17EN (1u << 18)
#define TIM1EN (1u << 11)
#define GPIOB_IDR 0x48000410u
#define TIM17 0x40014800u
#define TIM1 0x40012C00u
#define T_CR1 0x00u
#define T_DIER 0x0Cu
#define T_SR 0x10u
#define T_EGR 0x14u
#define T_CNT 0x24u
#define T_PSC 0x28u
#define T_ARR 0x2Cu
#define T_RCR 0x30u
#define T_CCR4 0x40u
#define T_DCR 0x48u
#define T_DMAR 0x4Cu
#define DMA 0x40020000u
#define DMA_IFCR (DMA + 0x04u)
#define CH(n) (DMA + 0x08u + 20u * ((n) - 1u)) /* CCRn; +4 CNDTR, +8 CPAR, +12 CMAR */
#define RSZ1 1024u
#define RSZ5 640u    /* pwm_audio's ring: 4 segments of 160 samples */
#define WIN_US 250000u
#ifndef N_CALLS
#define N_CALLS 20000u
#endif
#ifndef N_FD
#define N_FD 100u
#endif
#ifndef N_RAM
#define N_RAM 100000u
#endif

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

typedef struct {
  uint32_t last, acc;
} clk_t;

/* microseconds since clk_start, from TIM17's 16-bit count (read at least every 65 ms) */
static uint32_t clk(clk_t *c) {
  uint32_t t = rd(TIM17 + T_CNT) & 0xFFFFu;
  c->acc += (t - c->last) & 0xFFFFu;
  c->last = t;
  return c->acc;
}

static void clk_start(clk_t *c) {
  c->last = rd(TIM17 + T_CNT) & 0xFFFFu;
  c->acc = 0;
}

static void ch1_off(void) {
  wr(CH(1), 0);
  wr(DMA_IFCR, 0xFu);
}

static void ch1_on(uint8_t *ring, uint32_t pl) {
  ch1_off();
  wr(CH(1) + 8, GPIOB_IDR);
  wr(CH(1) + 12, (uint32_t)ring);
  wr(CH(1) + 4, RSZ1);
  wr(CH(1), (pl << 12) | (1u << 7) | (1u << 5) | 1u); /* MINC, CIRC, EN; 8-bit */
}

/* channel 5 and TIM1 as pwm_audio::start, without CC4E, MOE or PA11 */
static void ch5_on(uint16_t *ring) {
  wr(TIM1 + T_CR1, 0);
  wr(TIM1 + T_PSC, 0);
  wr(TIM1 + T_ARR, 2000);
  wr(TIM1 + T_RCR, 5);
  wr(TIM1 + T_CCR4, 1000);
  wr(TIM1 + T_DCR, 0x40u / 4);
  wr(TIM1 + T_DIER, 1u << 8); /* UDE */
  wr(TIM1 + T_EGR, 1);
  wr(CH(5), 0);
  wr(DMA_IFCR, 0xFu << 16);
  wr(CH(5) + 8, TIM1 + T_DMAR);
  wr(CH(5) + 12, (uint32_t)ring);
  wr(CH(5) + 4, RSZ5);
  /* PL high, 16/16, MINC, CIRC, memory to peripheral: pwm_audio's word */
  wr(CH(5), (2u << 12) | (1u << 10) | (1u << 8) | (1u << 7) | (1u << 5) | (1u << 4) | 1u);
  wr(TIM1 + T_CR1, (1u << 7) | 1u);
}

static void ch5_off(void) {
  wr(TIM1 + T_CR1, 0);
  wr(TIM1 + T_DIER, 0);
  wr(CH(5), 0);
  wr(DMA_IFCR, 0xFu << 16);
}

typedef struct {
  uint32_t n5, gap, laps, calls, fd, ram;
} res_t;

static void window(clk_t *c, res_t *r) {
  uint32_t t0 = clk(c), tl = t0, t = t0, v5 = rd(CH(5) + 4) & 0xFFFFu, v1 = rd(CH(1) + 4) & 0xFFFFu;
  r->n5 = r->gap = r->laps = 0;
  while (t - t0 < WIN_US) {
    uint32_t u5, u1;
    t = clk(c);
    u5 = rd(CH(5) + 4) & 0xFFFFu;
    u1 = rd(CH(1) + 4) & 0xFFFFu;
    if (u5 != v5) {
      uint32_t d = v5 - u5;
      if ((int32_t)d < 0) d += RSZ5;
      r->n5 += d;
      if (t - tl > r->gap) r->gap = t - tl;
      tl = t;
      v5 = u5;
    }
    if (u1 > v1) r->laps++;
    v1 = u1;
  }
  if (t - tl > r->gap) r->gap = t - tl; /* channel 5 stopped for good */
}

static void bench(const k6_api_t *api, clk_t *c, const uint16_t *ring5, res_t *r) {
  uint32_t i, s = 0, t;
  t = clk(c);
  for (i = 0; i < N_CALLS; i++) {
    s += api->ticks();
    if ((i & 255u) == 255u) clk(c); /* the clock's wraps: TIM17 laps in 65 ms */
  }
  r->calls = clk(c) - t;
  t = clk(c);
  for (i = 0; i < N_FD; i++) s += api->fd_read(0x67);
  r->fd = clk(c) - t;
  t = clk(c);
  for (i = 0; i < N_RAM; i++) s += ring5[i & 511u];
  r->ram = clk(c) - t;
  __asm volatile("" : : "r"(s));
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {
  char b[10];
  int n = 0;
  do {
    uint32_t q = 0, m = v, bit;
    for (bit = 1u << 28; bit; bit >>= 1) /* v / 10 without a divide */
      if (m >= 10u * bit) m -= 10u * bit, q += bit;
    b[n++] = (char)('0' + m);
    v = q;
  } while (v);
  while (n) *p++ = b[--n];
  return p;
}

static char *hex4(char *p, uint32_t v) {
  const char *x = "0123456789abcdef";
  int i;
  for (i = 12; i >= 0; i -= 4) *p++ = x[(v >> i) & 15];
  return p;
}

static const uint8_t PL[6] = {0xFF, 3, 0, 0xFF, 3, 0}; /* 0xFF: channel 1 off */

int k6_main(const k6_api_t *api) {
  uint32_t ahb = rd(RCC_AHBENR), apb2 = rd(RCC_APB2ENR), room = 0, i, n5[3] = {0, 0, 0};
  uint8_t *base = (uint8_t *)(((uint32_t)api->scratch(&room) + 3) & ~3u), *ring1;
  uint16_t *ring5;
  char line[64], *p;
  clk_t c;
  res_t r;
  int ret;
  if (room < RSZ1 + 2 * RSZ5 + 8) return -8;
  ring1 = base;
  ring5 = (uint16_t *)(base + RSZ1);
  wr(RCC_AHBENR, ahb | 1u); /* the DMA's registers read junk with its clock off */
  wr(RCC_APB2ENR, apb2 | TIM1EN);
  p = hex4(put(line, "dmash start ccr5="), rd(CH(5)));
  p = hex4(put(p, " cr1="), rd(TIM1 + T_CR1));
  p = dec(put(p, " room="), room);
  *p = 0;
  api->say(line);
  if ((apb2 & TIM17EN) || (rd(CH(1)) & 1)) {
    ret = -7;
    goto out;
  }
  if ((rd(CH(5)) & 1) || (rd(TIM1 + T_CR1) & 1)) {
    ret = -9;
    goto out;
  }
  for (i = 0; i < RSZ5; i++) ring5[i] = 1000;
  wr(RCC_APB2ENR, apb2 | TIM1EN | TIM17EN);
  wr(TIM17 + T_PSC, 95);
  wr(TIM17 + T_ARR, 0xFFFF);
  wr(TIM17 + T_EGR, 1);
  wr(TIM17 + T_SR, 0);
  wr(TIM17 + T_DIER, 1u << 8);
  wr(TIM17 + T_CR1, 1);
  clk_start(&c);
  ch5_on(ring5);
  for (i = 0; i < 6; i++) {
    if (PL[i] == 0xFF) ch1_off();
    else ch1_on(ring1, PL[i]);
    window(&c, &r);
    bench(api, &c, ring5, &r);
    ch1_off();
    if (i < 3) n5[i] = r.n5;
    p = dec(put(line, "w "), i);
    p = put(p, PL[i] == 0xFF ? " p=-" : PL[i] ? " p=3" : " p=0");
    p = dec(put(p, " n5="), r.n5);
    p = dec(put(p, " gap="), r.gap);
    p = dec(put(p, " laps="), r.laps);
    *p = 0;
    api->say(line);
    p = dec(put(line, "u "), i);
    p = put(p, PL[i] == 0xFF ? " p=-" : PL[i] ? " p=3" : " p=0");
    p = dec(put(p, " calls="), r.calls);
    p = dec(put(p, " fd="), r.fd);
    p = dec(put(p, " ram="), r.ram);
    *p = 0;
    api->say(line);
  }
  ch5_off();
  wr(TIM1 + T_RCR, 0);
  wr(TIM1 + T_DCR, 0);
  wr(TIM17 + T_CR1, 0);
  wr(TIM17 + T_DIER, 0);
  wr(TIM17 + T_PSC, 0);
  wr(TIM17 + T_SR, 0);
  p = dec(put(line, "dmash done n5="), n5[1]);
  p = dec(put(p, "/"), n5[0]);
  p = dec(put(p, "/"), n5[2]);
  *p = 0;
  api->say(line);
  ret = (int)((n5[1] << 16) | (n5[0] & 0xFFFFu));
out:
  wr(RCC_APB2ENR, apb2);
  wr(RCC_AHBENR, ahb);
  return ret;
}
