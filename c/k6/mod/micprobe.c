/* micprobe: with the mic ADC switched on, does the mic show anywhere the MCU can look?
   (2026-11-19, docs/k6-mic-probe.md; Jeff: "No flash: module writes the chip bus")

   The mic reaches only the FD6818. This switches the chip's mic ADC on while the K6 receives
   (REG_30 bit 2; then bits 2 and 1, the TX DSP too) and looks at everything the MCU can see,
   once with the room quiet and once with a tone sounding at the mic (another radio on
   445.850 MHz playing what the bench's Pluto sends there):
     af   PA11's converter (pa11adc.h) on the AF pin at each of REG_47's sixteen output selects
     gp   GP0's line (PB1) at each of REG_34's sixteen output selects: the share of ones in
          every GP_W reads
     rg   every register 0x00-0x7F (not 0x5F, the FSK FIFO): the least and the most of 6 reads
     mt   REG_64 (the VOX level) and REG_6F (the AF level), 104 reads each as fast as they come
     a0   PA0, the VOX pin: 64 conversions
   each in three states: 0 as found, 1 the mic ADC on, 2 the mic ADC and the TX DSP on.

   The order: wait until 440.850 is quiet (the upload's tail), measure (q=0), tune to 445.850
   and wait for a carrier that stays 8 s (the tone is on by then), tune back, measure (q=1),
   look at 445.850 once more (the tone must still be there), tune back.

   REG_30 holds the RX/TX state, and fd_write refuses it below API level 14. Where it does,
   micadc.h writes the register over the chip's bus pins itself, under level 14's own rule:
   only bits 2 and 1 change, never unless the chip is in receive with the PA off, read back.
   Nothing is keyed; the PA and its supply are never touched; the amplifier is off throughout.

   Lines (under 60 characters; tools/k6_micprobe.py reads them):
     micprobe start 30=bff1 47=6040 33=.... 34=.... 7d=e952 gp=1   (gp: PB1 is an input)
     micprobe quiet rssi=-72
     micprobe state q=0 st=1 30=bff5 rssi=-72 via=1    via: 1 the bus pins, 0 fd_write
     d 000 11f811e011f0...                 a capture's samples, 13 a line (gp, mt: only if they move)
     micprobe af q=0 st=1 sel=9 ticks=0092 lo=1a2b hi=1a40   (a sample: 0 .. 8100)
     micprobe gp q=0 st=1 sel=d ticks=00a3 lo=0000 hi=0258   (0 .. GP_W)
     r 00 llllhhhh llllhhhh ...            six registers a line from that address: least, most
     micprobe rg q=0 st=1
     micprobe mt q=0 st=1 reg=64 ticks=007a lo=000a hi=000a
     micprobe a0 q=0 st=1 lo=0000 hi=0010 sum=00000123
     micprobe sound rssi=-3a               (or: micprobe no sound)
     micprobe check rssi=-3a               445.850 after the q=1 pass
     micprobe done
   Writes, all put back: REG_30 (bits 2, 1), REG_47 (the select), REG_33 bit 8 and REG_34 (GP0),
   the tuning, PA11's mode, speed and output bit, PB2, the ADC's channel select. Returns the
   passes made (6), or -8 (scratch under 1 KB), -6 (a key), -5 (REG_30 would not take the bits
   or the chip is not in receive). Level 4.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 4
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "micadc.h"
#include "pa11adc.h"

#define GPIOB 0x48000400u
#define B_MODER 0x00u
#define B_IDR 0x10u
#define B_ODR 0x14u
#define B_BSRR 0x18u
#define B_BRR 0x28u
#define PB1 (1u << 1)
#define AMP (1u << 2)
#define ADC 0x40012400u
#define ADC_ISR (ADC + 0x00u)
#define ADC_CR (ADC + 0x08u)
#define ADC_CHSELR (ADC + 0x28u)
#define ADC_DR (ADC + 0x40u)
#ifndef SOUND_HZ
#define SOUND_HZ 445850000u
#endif
#ifndef N_CAP
#define N_CAP 260u /* 20 lines of 13 */
#endif
#ifndef PRE
#define PRE 2400u /* samples before the ones kept: the 1 uF coupling capacitor settles */
#endif
#ifndef GP_W
#define GP_W 600u /* reads of PB1 a sample: about 16 kHz */
#endif
#ifndef QUIET_WAIT_MS
#define QUIET_WAIT_MS 30000u
#endif
#ifndef HOLD_MS
#define HOLD_MS 8000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 120000u
#endif
#define RSSI_ON -85 /* dBm */
#define HOLD 3u
#define W 90u

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hexn(char *p, uint32_t v, int n) {
  const char *x = "0123456789abcdef";
  int i;
  for (i = 4 * (n - 1); i >= 0; i -= 4) *p++ = x[(v >> i) & 15];
  return p;
}

static char *dbm(char *p, int v) {
  if (v < 0) {
    *p++ = '-';
    v = -v;
  }
  return hexn(p, (uint32_t)v, 2);
}

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }

typedef struct {
  const k6_api_t *api;
  uint16_t *buf;
  char *line;    /* 64 bytes, in the scratch like buf: the stack is the core's, and short */
  uint32_t m_in; /* GPIOA's MODER with PA11 an input */
  uint8_t q, st, gp0;
} run_t;

/* a capture's samples (if they move by flat or more) and its line */
static void emit(const run_t *r, const char *kind, uint32_t sel, uint32_t n, uint32_t ticks, uint32_t flat) {
  const k6_api_t *api = r->api;
  char *line = r->line, *p;
  uint32_t i, j, lo = 0xFFFFu, hi = 0;
  for (i = 0; i < n; i++) {
    if (r->buf[i] < lo) lo = r->buf[i];
    if (r->buf[i] > hi) hi = r->buf[i];
  }
  if (hi - lo >= flat) {
    for (i = 0; i < n; i += 13) {
      p = hexn(put(line, "d "), i, 3);
      *p++ = ' ';
      for (j = i; j < i + 13 && j < n; j++) p = hexn(p, r->buf[j], 4);
      *p = 0;
      api->say(line);
    }
  }
  p = put(put(line, "micprobe "), kind);
  p = hexn(put(p, " q="), r->q, 1);
  p = hexn(put(p, " st="), r->st, 1);
  p = hexn(put(p, kind[0] == 'm' ? " reg=" : " sel="), sel, kind[0] == 'm' ? 2 : 1);
  p = hexn(put(p, " ticks="), ticks & 0xFFFFu, 4);
  p = hexn(put(p, " lo="), lo, 4);
  p = hexn(put(p, " hi="), hi, 4);
  *p = 0;
  api->say(line);
  api->delay_us(30000); /* the serial port drains before the next capture */
}

/* the AF pin at each of REG_47's selects */
static void af_sweep(const run_t *r, uint32_t r47) {
  const k6_api_t *api = r->api;
  uint32_t sel, i, t0 = 0;
  for (sel = 0; sel < 16; sel++) {
    pa11_t c = {0, 0, 0, 0};
    api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (sel << 8)));
    for (i = 0; i < PRE + N_CAP; i++) {
      uint32_t y;
      if (i == PRE) t0 = api->ticks();
      y = pa11_sample(r->m_in, HOLD, W, &c);
      if (i >= PRE) r->buf[i - PRE] = (uint16_t)y;
    }
    t0 = api->ticks() - t0;
    emit(r, "af", sel, N_CAP, t0, 0);
  }
  api->fd_write(0x47, (uint16_t)r47);
}

static uint32_t __attribute__((noinline)) gp_sample(uint32_t w) {
  volatile uint32_t *idr = (volatile uint32_t *)(GPIOB + B_IDR);
  uint32_t n = 0;
  do {
    n += (*idr >> 1) & 1u;
  } while (--w);
  return n;
}

/* GP0's line at each of REG_34's selects (REG_33 bit 8 clear: GP0 drives) */
static void gp_sweep(const run_t *r, uint32_t r33, uint32_t r34) {
  const k6_api_t *api = r->api;
  uint32_t sel, i, t0;
  if (api->fd_write(0x33, (uint16_t)(r33 & ~0x0100u)) != 0) return;
  for (sel = 0; sel < 16; sel++) {
    api->fd_write(0x34, (uint16_t)((r34 & ~0x000Fu) | sel));
    api->delay_us(2000);
    t0 = api->ticks();
    for (i = 0; i < N_CAP; i++) r->buf[i] = (uint16_t)gp_sample(GP_W);
    t0 = api->ticks() - t0;
    emit(r, "gp", sel, N_CAP, t0, 2);
  }
  api->fd_write(0x34, (uint16_t)r34);
  api->fd_write(0x33, (uint16_t)r33);
}

static char *qst(const run_t *r, char *p) {
  p = hexn(put(p, " q="), r->q, 1);
  return hexn(put(p, " st="), r->st, 1);
}

/* every register: the least and the most of six reads */
static void reg_scan(const run_t *r) {
  const k6_api_t *api = r->api;
  char *line = r->line, *p = line;
  uint32_t reg, k;
  for (reg = 0; reg < 0x80u; reg++) {
    uint32_t lo = 0xFFFFu, hi = 0;
    if (reg % 6u == 0) p = hexn(put(line, "r "), reg, 2);
    for (k = 0; k < 6 && reg != 0x5Fu; k++) {
      uint32_t v = api->fd_read((uint8_t)reg);
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    *p++ = ' ';
    p = hexn(hexn(p, lo, 4), hi, 4);
    if (reg % 6u == 5 || reg == 0x7Fu) {
      *p = 0;
      api->say(line);
    }
  }
  *qst(r, put(line, "micprobe rg")) = 0;
  api->say(line);
  api->delay_us(30000);
}

/* a meter register as fast as it reads */
static void meter(const run_t *r, uint32_t reg) {
  const k6_api_t *api = r->api;
  uint32_t i, t0 = api->ticks();
  for (i = 0; i < 104; i++) r->buf[i] = api->fd_read((uint8_t)reg);
  t0 = api->ticks() - t0;
  emit(r, "mt", reg, 104, t0, 1);
}

/* PA0, the VOX pin: 64 conversions of ADC channel 0 */
static void pa0(const run_t *r) {
  const k6_api_t *api = r->api;
  char *line = r->line, *p;
  uint32_t ch = rd(ADC_CHSELR), i, spin, lo = 0xFFFFu, hi = 0, sum = 0;
  wr(ADC_CHSELR, 1u);
  for (i = 0; i < 64; i++) {
    uint32_t v;
    wr(ADC_CR, rd(ADC_CR) | (1u << 2)); /* ADSTART */
    for (spin = 0; !(rd(ADC_ISR) & (1u << 2)); spin++)
      if (spin > 20000u) goto out;
    v = rd(ADC_DR) & 0xFFFFu;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    sum += v;
  }
  p = qst(r, put(line, "micprobe a0"));
  p = hexn(put(p, " lo="), lo, 4);
  p = hexn(put(p, " hi="), hi, 4);
  p = hexn(put(p, " sum="), sum, 8);
  *p = 0;
  api->say(line);
out:
  wr(ADC_CHSELR, ch);
}

/* one pass: the three states with the room as it is. Returns the states done, or under 0. */
static int pass(run_t *r, uint32_t r47, uint32_t r33, uint32_t r34) {
  const k6_api_t *api = r->api;
  char *line = r->line, *p;
  uint32_t st;
  int via;
  for (st = 0; st < 3; st++) {
    if (api->key_poll()) return -6;
    r->st = (uint8_t)st;
    via = mic_state(api, st == 0 ? 0u : st == 1 ? MIC_ADC : MIC_BITS);
    if (via < 0) return -5;
    api->delay_us(50000);
    p = qst(r, put(line, "micprobe state"));
    p = hexn(put(p, " 30="), api->fd_read(0x30), 4);
    p = dbm(put(p, " rssi="), rssi(api));
    p = hexn(put(p, " via="), (uint32_t)via, 1);
    *p = 0;
    api->say(line);
    api->delay_us(30000);
    af_sweep(r, r47);
    if (r->gp0) gp_sweep(r, r33, r34);
    reg_scan(r);
    meter(r, 0x64);
    meter(r, 0x6F);
    pa0(r);
  }
  return mic_state(api, 0) < 0 ? -5 : 3;
}

int k6_main(const k6_api_t *api) {
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(GPIOB + B_ODR) & AMP, pbm = rd(GPIOB + B_MODER),
           home = api->rx_hz(), room = 0, t0, t1 = 0, up = 0;
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  uint16_t r47 = api->fd_read(0x47), r33 = api->fd_read(0x33), r34 = api->fd_read(0x34);
  run_t r;
  char *line, *p;
  int ret = 0, n;
  r.api = api;
  r.line = line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  r.buf = (uint16_t *)(line + 64);
  r.m_in = moder & ~(3u << (2 * PA11_PIN));
  r.q = r.st = 0;
  r.gp0 = (pbm & (3u << 2)) == 0; /* PB1 an input, as Aura leaves it */
  if (room < 1024) return -8;
  p = hexn(put(line, "micprobe start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 47="), r47, 4);
  p = hexn(put(p, " 33="), r33, 4);
  p = hexn(put(p, " 34="), r34, 4);
  p = hexn(put(p, " 7d="), api->fd_read(0x7D), 4);
  p = hexn(put(p, " gp="), r.gp0, 1);
  *p = 0;
  api->say(line);
  wr(GPIOB + B_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  /* the upload's tail: wait until the channel has been quiet for a second */
  t0 = api->ms();
  t1 = t0;
  while (api->ms() - t0 < QUIET_WAIT_MS && api->ms() - t1 < 1000u) {
    if (rssi(api) >= RSSI_ON) t1 = api->ms();
    if (api->key_poll()) {
      ret = -6;
      goto out;
    }
    api->delay_us(20000);
  }
  *dbm(put(line, "micprobe quiet rssi="), rssi(api)) = 0;
  api->say(line);
  n = pass(&r, r47, r33, r34);
  if (n < 0) {
    ret = n;
    goto out;
  }
  ret += n;
  /* the sound: a carrier on SOUND_HZ that stays HOLD_MS (its ID over, the tone on) */
  api->tune(SOUND_HZ, wide);
  t0 = api->ms();
  for (;;) {
    if (rssi(api) < RSSI_ON) {
      up = 0;
    } else if (!up) {
      up = 1;
      t1 = api->ms();
    } else if (api->ms() - t1 >= HOLD_MS) {
      break;
    }
    if (api->key_poll()) {
      ret = -6;
      goto out;
    }
    if (api->ms() - t0 >= WAIT_MS) {
      api->say("micprobe no sound");
      goto out;
    }
    api->delay_us(20000);
  }
  *dbm(put(line, "micprobe sound rssi="), rssi(api)) = 0;
  api->say(line);
  api->tune(home, wide);
  api->delay_us(300000);
  r.q = 1;
  n = pass(&r, r47, r33, r34);
  if (n < 0) {
    ret = n;
    goto out;
  }
  ret += n;
  api->tune(SOUND_HZ, wide);
  api->delay_us(300000);
  *dbm(put(line, "micprobe check rssi="), rssi(api)) = 0;
  api->say(line);
out:
  mic_state(api, 0);
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r47);
  api->fd_write(0x34, r34);
  api->fd_write(0x33, r33);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(GPIOB + B_BSRR, AMP);
  api->say("micprobe done");
  return ret;
}
