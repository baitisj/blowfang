/* dmairq: can a K6 module take DMA channel 1's interrupt? (2026-11-20, for the FM-in sotto
   encoder: Jeff, "receive FM audio and then encode").

   The IF tap (GP0 code 13 on PB1, gp0rx.h) runs DMA channel 1 flat out into a ring (about
   5.6 M bytes/s), which a 1 KB ring holds for only 0.18 ms. The FT8 and APRS modules poll it.
   The slim sotto encoder runs in bursts of several ms, so beside it the ring has to be drained
   from the DMA's half-transfer and transfer-complete interrupts instead.

   The KD32F328 has no VTOR: an interrupt reaches a module only through the SRAM copy of the
   vector table that txdog uses (docs/k6-modules.md, txdog), with SRAM mapped at 0 (SYSCFG
   CFGR1 MEM_MODE 3). This checks the copy (every entry but HardFault and TIM14 the
   bootloader's, as vectab does), points its DMA1_Channel1 entry (IRQ 9) at a handler here,
   maps SRAM at 0, and runs the tap with both interrupts on for RUN_MS while the CPU stays busy
   in long bursts with interrupts on, as the encoder will. The handler counts the halves, the ones
   on PB1, the halves it missed (both flags at once) and its own time on TIM17. Then: a say and
   an fl_read with it running, everything put back. Safety: the handler turns its interrupt off
   by itself after MAX_IRQ halves with no word from the foreground, so a stuck foreground can't
   leave an interrupt storm; a bad vector faults into Aura's HardFault, which reboots.

   Lines (under 60 characters):
     dmairq start cfgr1=00000002 v25=080001d1 bad=00 hf=1
     dmairq run ms=2000 h=22187 miss=0 isr=31 ks=5680 ones=49
     dmairq calls say=1 fl=0 h=571 miss=0
     dmairq done cfgr1=00000002 acr=12 v25=080001d1
   bad: entries of the copy that differ from the bootloader's (not HardFault or TIM14); hf: its
   HardFault is not the bootloader's (txdog's). h: halves taken; miss: halves lost; isr: the
   handler's share of the CPU, %; ks: DMA kilo-transfers a second; ones: % of samples high.
   Writes REG_34[3:0] and REG_33 bit 8 (GP0), SYSCFG CFGR1 and FLASH_ACR, the copy's entry 25 and 46, NVIC
   bit 9, TIM17, DMA channel 1 and their clock enables, all put back. Receive only. Returns
   h of the run, or -2 (a key), -4 (PB1 not an input), -5 (REG_33 refused), -7 (TIM17 or DMA
   channel 1 in use), -8 (scratch), -9 (the copy isn't the bootloader's table).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "m0rt.h" /* division for the report */

#define GPIOB 0x48000400u
#define RCC_AHBENR 0x40021014u
#define RCC_APB2ENR 0x40021018u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define DMA 0x40020000u
#define DMA_ISR DMA
#define DMA_IFCR (DMA + 0x04)
#define DMA_CCR1 (DMA + 0x08)
#define DMA_CNDTR1 (DMA + 0x0C)
#define DMA_CPAR1 (DMA + 0x10)
#define DMA_CMAR1 (DMA + 0x14)
#define SYSCFG_CFGR1 0x40010000u
#define FLASH_ACR 0x40022000u /* MEM_MODE 3 drops its prefetch bit; put back after */
#define NVIC_ISER 0xE000E100u
#define NVIC_ICER 0xE000E180u
#define NVIC_ICPR 0xE000E280u
#define SRAM0 0x20000000u
#define BOOT_VEC 0x08000000u
#define NVEC 48u
#define V_HF 3u
#define V_TIM14 (16u + 19u)
#define V_DMA1 (16u + 9u)
#define V_CTX 46u /* an unused entry: the handler's state pointer */
#define IRQ_DMA1 9u
#define TIM17EN (1u << 18)
#define SYSCFGEN 1u
#ifndef RSZ
#define RSZ 1024u
#endif
#ifndef RUN_MS
#define RUN_MS 2000u
#endif
#ifndef BURST_US
#define BURST_US 4000u /* the foreground's busy bursts: an encoder call's length, and more */
#endif
#define MAX_IRQ 40000u /* halves with no word from the foreground: the handler stops itself */

static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }

typedef struct {
  volatile uint32_t halves, miss, ones, cyc, quiet;
  uint32_t c17; /* TIM17 at the handler's last exit, for its unwrapped time */
  uint8_t *ring;
} ctx_t;

/* DMA1_Channel1: one half of the ring ready (HTIF: the first, TCIF: the second) */
static void dma_isr(void) {
  ctx_t *c = (ctx_t *)rd(SRAM0 + 4u * V_CTX);
  uint32_t t0 = rd(TIM17_CNT) & 0xFFFF, f = rd(DMA_ISR) & 7u, n = 0, i;
  const uint32_t *w;
  wr(DMA_IFCR, 0xFu); /* ch 1's flags, all */
  if ((f & 6u) == 6u) c->miss++;
  w = (const uint32_t *)(c->ring + ((f & 4u) && !(f & 2u) ? 0u : RSZ / 2u));
  for (i = 0; i < RSZ / 8u; i++) {
    uint32_t v = w[i] & 0x02020202u; /* PB1 in each byte */
    v = (v >> 1) + (v >> 9);
    n += (v + (v >> 16)) & 0xFFu;
  }
  c->ones += n;
  c->halves++;
  if (++c->quiet > MAX_IRQ) { /* the foreground stopped talking: stop */
    wr(DMA_CCR1, rd(DMA_CCR1) & ~6u);
    wr(NVIC_ICER, 1u << IRQ_DMA1);
  }
  c->cyc += ((rd(TIM17_CNT) & 0xFFFF) - t0) & 0xFFFF;
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hex(char *p, uint32_t v, int digits) {
  const char *x = "0123456789abcdef";
  while (digits--) *p++ = x[(v >> (4 * digits)) & 15];
  return p;
}

static char *dec(char *p, uint32_t v) {
  char t[10];
  int n = 0;
  do {
    t[n++] = (char)('0' + v % 10u);
    v /= 10u;
  } while (v);
  while (n) *p++ = t[--n];
  return p;
}

static uint32_t udiv(uint32_t a, uint32_t b) { return b ? a / b : 0; }

/* busy for about us microseconds without a module call, interrupts on */
static void burn(uint32_t us) {
  volatile uint32_t n = us * 24u; /* ~4 cycles a turn at 96 MHz */
  while (n) n--;
}

int k6_main(const k6_api_t *api) {
  uint32_t room = 0, i, bad = 0, apb2, ahb, cfgr1, acr, v25, v46, t0, h0, m0, c0, o0, dt, back;
  uint16_t r33, r34;
  char line[64], *p;
  ctx_t *c;
  uint8_t *ring;
  int ret = 0, hf;
  uint8_t *s = (uint8_t *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  if (room < RSZ + sizeof(ctx_t) + 8) return -8;
  ring = s;
  c = (ctx_t *)(s + RSZ);
  /* the copy: the bootloader's table but for HardFault and TIM14 */
  for (i = 2; i < NVEC; i++)
    if (i != V_HF && i != V_TIM14 && i != V_CTX && i != V_CTX + 1 && rd(SRAM0 + 4u * i) != rd(BOOT_VEC + 4u * i)) bad++;
  hf = rd(SRAM0 + 4u * V_HF) != rd(BOOT_VEC + 4u * V_HF);
  apb2 = rd(RCC_APB2ENR);
  wr(RCC_APB2ENR, apb2 | SYSCFGEN);
  cfgr1 = rd(SYSCFG_CFGR1);
  acr = rd(FLASH_ACR);
  v25 = rd(SRAM0 + 4u * V_DMA1);
  v46 = rd(SRAM0 + 4u * V_CTX);
  p = put(line, "dmairq start cfgr1=");
  p = hex(p, cfgr1, 8);
  p = hex(put(p, " v25="), v25, 8);
  p = hex(put(p, " bad="), bad, 2);
  p = hex(put(p, " hf="), (uint32_t)hf, 1);
  *p = 0;
  api->say(line);
  if (bad || !hf) {
    wr(RCC_APB2ENR, apb2);
    return -9;
  }
  /* the upload's carrier gone */
  t0 = api->ms();
  while (KM_RSSI_DBM(api->fd_read(0x67)) > -80 && api->ms() - t0 < 30000) {
    if (api->key_down()) {
      wr(RCC_APB2ENR, apb2);
      return -2;
    }
    api->delay_us(10000);
  }
  api->delay_us(200000);
  if (rd(GPIOB) & (3u << 2)) {
    wr(RCC_APB2ENR, apb2);
    return -4;
  }
  ahb = rd(RCC_AHBENR);
  wr(RCC_AHBENR, ahb | 1u);
  if ((apb2 & TIM17EN) || (rd(DMA_CCR1) & 1)) {
    wr(RCC_AHBENR, ahb);
    wr(RCC_APB2ENR, apb2);
    return -7;
  }
  r33 = api->fd_read(0x33);
  r34 = api->fd_read(0x34);
  if ((r33 & 0x0100) && api->fd_write(0x33, (uint16_t)(r33 & ~0x0100)) != 0) {
    wr(RCC_AHBENR, ahb);
    wr(RCC_APB2ENR, apb2);
    return -5;
  }
  api->fd_write(0x34, (uint16_t)((r34 & 0xFFF0) | 13));
  wr(RCC_APB2ENR, apb2 | SYSCFGEN | TIM17EN);
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x28, 0);
  wr(TIM17 + 0x2C, 0xFFFF);
  wr(TIM17 + 0x14, 1);
  wr(TIM17 + 0x10, 0);
  wr(TIM17 + 0x0C, 1u << 8); /* the update DMA request starts channel 1 (fmsotto.c, fmso1d) */
  wr(TIM17 + 0x00, 1);
  c->halves = c->miss = c->ones = c->cyc = c->quiet = 0;
  c->ring = ring;
  /* the handler in: its state, its vector, SRAM at 0, then the DMA with both interrupts */
  wr(SRAM0 + 4u * V_CTX, (uint32_t)c);
  wr(SRAM0 + 4u * V_DMA1, (uint32_t)&dma_isr | 1u); /* (Thumb) */
  wr(SYSCFG_CFGR1, (cfgr1 & ~3u) | 3u);
  wr(DMA_CCR1, 0);
  wr(DMA_IFCR, 0xF);
  wr(DMA_CPAR1, GPIOB + 0x10);
  wr(DMA_CMAR1, (uint32_t)ring);
  wr(DMA_CNDTR1, RSZ);
  wr(NVIC_ICPR, 1u << IRQ_DMA1);
  wr(NVIC_ISER, 1u << IRQ_DMA1);
  wr(DMA_CCR1, (3u << 12) | (1u << 7) | (1u << 5) | (1u << 2) | (1u << 1) | 1u); /* +HTIE, TCIE */
  /* the run: busy bursts with interrupts on, a word to the handler between them */
  t0 = api->ms();
  h0 = c->halves, m0 = c->miss, c0 = c->cyc, o0 = c->ones;
  while (api->ms() - t0 < RUN_MS) {
    burn(BURST_US);
    api->delay_us(20); /* (a module call between bursts, as the encoder's loop will make) */
    c->quiet = 0;
    if (api->key_poll()) {
      ret = -2;
      break;
    }
  }
  dt = api->ms() - t0;
  h0 = c->halves - h0;
  m0 = c->miss - m0;
  c0 = c->cyc - c0;
  o0 = c->ones - o0;
  if (ret == 0) ret = (int)h0;
  p = dec(put(line, "dmairq run ms="), dt);
  p = dec(put(p, " h="), h0);
  p = dec(put(p, " miss="), m0);
  p = dec(put(p, " isr="), udiv(c0, dt * 960u)); /* cycles over ms x 96,000, as % */
  p = dec(put(p, " ks="), udiv(h0 * (RSZ / 2u), dt));
  p = dec(put(p, " ones="), udiv(o0, udiv(h0 * (RSZ / 2u), 100u) + 1u));
  *p = 0;
  /* the firmware's calls with the handler running: a say and an fl_read */
  h0 = c->halves, m0 = c->miss;
  api->say(line);
  {
    uint8_t b[20];
    int fl = api->level >= 5 ? api->fl_read(0x100000u, b, sizeof b) : 9;
    for (i = 0; i < 5; i++) {
      api->delay_us(10000);
      c->quiet = 0;
    }
    p = put(line, "dmairq calls say=1 fl=");
    p = dec(p, (uint32_t)(fl < 0 ? 8 : fl));
    p = dec(put(p, " h="), c->halves - h0);
    p = dec(put(p, " miss="), c->miss - m0);
    *p = 0;
  }
  /* everything back: the interrupt off first, then the DMA, the map, the vector */
  wr(NVIC_ICER, 1u << IRQ_DMA1);
  wr(DMA_CCR1, 0);
  wr(DMA_IFCR, 0xF);
  wr(NVIC_ICPR, 1u << IRQ_DMA1);
  wr(SYSCFG_CFGR1, cfgr1);
  wr(FLASH_ACR, acr);
  back = rd(SYSCFG_CFGR1);
  wr(SRAM0 + 4u * V_DMA1, v25);
  wr(SRAM0 + 4u * V_CTX, v46);
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x0C, 0);
  wr(RCC_APB2ENR, apb2);
  wr(RCC_AHBENR, ahb);
  api->fd_write(0x34, r34);
  if (r33 & 0x0100) api->fd_write(0x33, r33);
  api->say(line);
  p = hex(put(line, "dmairq done cfgr1="), back, 8);
  p = hex(put(p, " acr="), rd(FLASH_ACR), 2);
  p = hex(put(p, " v25="), rd(SRAM0 + 4u * V_DMA1), 8);
  *p = 0;
  api->say(line);
  return ret;
}
