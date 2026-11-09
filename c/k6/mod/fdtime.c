/* fdtime: how long the module API's calls take on this image (2026-11-09). EXPLORE's DMR
   detector (c/k6/xdmr.c) drains its DMA ring in xlisten's waits; between two waits come the
   tone detector's read (td_read: FD6818 REG_0B and REG_0C) and two api->ms() calls. On txdog
   images that gap outlasts the ring at every wait (xdmrlisten, 21:51Z: `DMR diag e=29681 o=0
   g=2221`, 30 a step), and the same build without txdog loses ~0.4 a step. This times N calls
   of each, with TIM17 at 96 MHz unwrapped by Aura's 10 kHz TIM14 (as xdmr does), interrupts
   on, and says, times in 0.1 us:
     fdt rd0B min=1234 avg=1234 max=12345      api->fd_read(0x0B)
     fdt td min= avg= max=                     fd_read(0x0B) + fd_read(0x0C), td_read's usual pair
     fdt ms min= avg= max=                     api->ms()
     fdt tk min= avg= max=                     api->ticks()
   TIM17 and its clock enable are put back. Receive only, writes no radio register. Level 2
   (fd_read).
   fdirq (FD_IRQ, 2026-11-09): PR #23's image read ACR 0x32 at rest, yet the td pair took 382 us
   with interrupts on against pfprobe stage 4's 195 us with them off. It also says
     fdt irq iser=........ ispr=........ syst=........ rvr=........ icsr=........
                                               NVIC enabled/pending, SysTick CSR/RVR, ICSR
     fdt td-cli min= avg= max=                 the td pair with interrupts off (PRIMASK)
     fdt spin on=1234 off=1234                 a counting loop's passes in 2 ms, interrupts on
                                               and off: on/off is the CPU share left to code
   fdtick (FD_TICKOFF, 2026-11-09): fdirq read SysTick CSR 0x00010006 between delays, TICKINT
   on, so each 1 us delay of the FD6818 bit-bang ended in a SysTick exception through the
   bootloader's table (td 382 us; 196 us under PRIMASK). It clears TICKINT first, for good
   (until the next reboot), then runs as fdirq, and says `fdt tickint was=1` (or 0).
   SPDX-License-Identifier: MIT */
#ifdef FD_TICKOFF
#define FD_IRQ
#endif
#define K6_MODULE_LEVEL 2
#include "../k6_mod.h"

#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT 0x40002024u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define TIM17EN (1u << 18)
#define N 200u

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

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

/* the M0 has no divide instruction and a module no runtime library */
__attribute__((noinline)) static uint32_t udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
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

/* cycles -> 0.1 us */
static uint32_t tenths(uint32_t cyc) { return udiv(cyc * 10u + 48u, 96u); }

static void one(const k6_api_t *api, clk_t *c, int what, const char *name, int cli) {
  uint32_t i, t0, dt, mn = 0xFFFFFFFFu, mx = 0, sum = 0, s = 0;
  char line[60], *p;
  if (cli) __asm volatile("cpsid i" ::: "memory");
  for (i = 0; i < N; i++) {
    t0 = now(c);
    if (what == 0) s += api->fd_read(0x0B);
    else if (what == 1) s += api->fd_read(0x0B), s += api->fd_read(0x0C);
    else if (what == 2) s += api->ms();
    else s += api->ticks();
    dt = now(c) - t0;
    if (dt < mn) mn = dt;
    if (dt > mx) mx = dt;
    sum += dt;
  }
  if (cli) __asm volatile("cpsie i" ::: "memory");   /* before say: its queue drains by IRQ */
  __asm volatile("" : : "r"(s));
  p = put(put(put(line, "fdt "), name), " min=");
  p = put(dec(p, tenths(mn)), " avg=");
  p = put(dec(p, tenths(udiv(sum, N))), " max=");
  p = dec(p, tenths(mx));
  *p = 0;
  api->say(line);
}

#ifdef FD_IRQ
static char *hex(char *p, uint32_t v) {
  int i;
  for (i = 28; i >= 0; i -= 4) {
    uint32_t d = (v >> i) & 15u;
    *p++ = (char)(d < 10u ? '0' + d : 'a' + d - 10u);
  }
  return p;
}

/* passes of a counting loop in 2 ms of TIM17 */
static uint32_t spin(clk_t *c, int cli) {
  uint32_t t0, n = 0;
  if (cli) __asm volatile("cpsid i" ::: "memory");
  t0 = now(c);
  while (now(c) - t0 < 96u * 2000u) n++;
  if (cli) __asm volatile("cpsie i" ::: "memory");
  return n;
}

static void irq(const k6_api_t *api, clk_t *c) {
  char line[96], *p;
  uint32_t on, off;
  p = hex(put(line, "fdt irq iser="), rd(0xE000E100u));
  p = hex(put(p, " ispr="), rd(0xE000E200u));
  *p = 0;
  api->say(line);
  p = hex(put(line, "fdt syst="), rd(0xE000E010u));
  p = hex(put(p, " rvr="), rd(0xE000E014u));
  p = hex(put(p, " icsr="), rd(0xE000ED04u));
  *p = 0;
  api->say(line);
  one(api, c, 1, "td-cli", 1);
  on = spin(c, 0), off = spin(c, 1);
  p = dec(put(line, "fdt spin on="), on);
  p = dec(put(p, " off="), off);
  *p = 0;
  api->say(line);
}
#endif

int k6_main(const k6_api_t *api) {
  uint32_t apb2 = rd(RCC_APB2ENR), r[4], i;
#ifdef FD_TICKOFF
  uint32_t csr = rd(0xE000E010u);
  wr(0xE000E010u, csr & ~2u);
  api->say((csr & 2u) ? "fdt tickint was=1" : "fdt tickint was=0");
#endif
  static const uint32_t off[4] = {0x00, 0x28, 0x2C, 0x0C};   /* CR1 PSC ARR DIER */
  clk_t c;
  wr(RCC_APB2ENR, apb2 | TIM17EN);
  for (i = 0; i < 4; i++) r[i] = rd(TIM17 + off[i]);
  wr(TIM17 + 0x00, 0), wr(TIM17 + 0x0C, 0), wr(TIM17 + 0x28, 0), wr(TIM17 + 0x2C, 0xFFFF);
  wr(TIM17 + 0x14, 1), wr(TIM17 + 0x10, 0), wr(TIM17 + 0x00, 1);
  c.t = 0, c.c17 = rd(TIM17_CNT) & 0xFFFF, c.c14 = rd(TIM14_CNT) & 0xFFFF;
  one(api, &c, 0, "rd0B", 0);
  one(api, &c, 1, "td", 0);
  one(api, &c, 2, "ms", 0);
  one(api, &c, 3, "tk", 0);
#ifdef FD_IRQ
  irq(api, &c);
#endif
  wr(TIM17 + 0x00, 0);
  for (i = 3; i < 4; i--) wr(TIM17 + off[i], r[i]);
  wr(RCC_APB2ENR, apb2);
  return 0;
}
