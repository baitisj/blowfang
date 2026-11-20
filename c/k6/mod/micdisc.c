/* micdisc: the chip's "DISC baseband" mode as a way from the mic to the AF pin (2026-11-20,
   docs/k6-mic-probe.md). The FD6818 guide has a mode for sets whose receiver is another chip:
   that receiver's discriminator goes into MICIN, and the FD6818 runs it through its receive
   audio chain (filters, de-emphasis, sub-audio and DTMF decoders) to AFOUT. The guide switches
   it on with REG_44 bit 0 and the mic ADC beside the RX DSP. If the K6's chip (ID 0x4819) has
   the mode at the same bit, the mic's audio comes out of the AF pin as received audio does,
   where PA11's converter (pa11adc.h) reads it.

   22 captures of 598 samples with the room quiet (tags 0..9, a..l), and the same 22 (A..V) once
   a carrier has stayed 5 s on SOUND_HZ (another radio there plays the bench's tone at the mic;
   the bench sends it weak, so that it does not leak into the K6's receiver by radio), the K6
   back on its own channel:
     0        as found, select 1: the receiver's hiss
     1        the mic ADC on, select 1
     2        REG_44 bit 0 flipped, the mic ADC off, select 1
     3 .. i   REG_44 bit 0 flipped and the mic ADC on, selects 0 .. 15
     j, k, l  the same with the TX DSP on too, selects 1, 4, 9
   Lines (under 60 characters; samples are four hex digits, 13 a line):
     micdisc start 30=bff1 44=8ed8 47=6040 7e=37ee
     3 000 11f811e011f0...
     micdisc 3 ticks=0150 st=1 d=1 sel=0 44=8ed9 rssi=-72
     micdisc sound rssi=-4f           (or: micdisc no sound)
     micdisc check rssi=-4f
     micdisc done
   Writes, all put back: REG_30 (bits 2, 1, by micadc.h's rule), REG_44 (bit 0), REG_47 (the
   select), the tuning, PA11's mode, speed and output bit, PB2. Returns the captures made, or -8
   (scratch under 1.3 KB), -6 (a key), -5 (REG_30 would not take the bits, or the chip is not in
   receive with the PA off). Level 4.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 4
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "micadc.h"
#include "pa11adc.h"

#define AMP (1u << 2)
#ifndef SOUND_HZ
#define SOUND_HZ 445850000u
#endif
#ifndef N
#define N 598u /* 46 lines of 13 */
#endif
#ifndef PRE
#define PRE 2400u /* samples before the ones kept: the 1 uF coupling capacitor settles */
#endif
#ifndef QUIET_WAIT_MS
#define QUIET_WAIT_MS 30000u
#endif
#ifndef HOLD_MS
#define HOLD_MS 5000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 120000u
#endif
#define RSSI_ON -90 /* dBm: the sound's carrier is sent weak */
#define HOLD 3u
#define W 90u
#define DISC 0x0001u /* REG_44 bit 0, by the guide */
#define N_CFG 22u

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
  char *line; /* 64 bytes, in the scratch like buf */
  uint16_t *buf;
  uint32_t m_in;
} run_t;

/* one capture and its dump */
static void capture(const run_t *r, char tag, uint32_t st, uint32_t disc, uint32_t sel) {
  const k6_api_t *api = r->api;
  char *line = r->line, *p;
  uint32_t i, j, t0 = 0;
  pa11_t c = {0, 0, 0, 0};
  for (i = 0; i < PRE + N; i++) {
    uint32_t y;
    if (i == PRE) t0 = api->ticks();
    y = pa11_sample(r->m_in, HOLD, W, &c);
    if (i >= PRE) r->buf[i - PRE] = (uint16_t)y;
  }
  t0 = (api->ticks() - t0) & 0xFFFFu;
  for (i = 0; i < N; i += 13) {
    line[0] = tag;
    line[1] = ' ';
    p = hexn(line + 2, i, 3);
    *p++ = ' ';
    for (j = i; j < i + 13 && j < N; j++) p = hexn(p, r->buf[j], 4);
    *p = 0;
    api->say(line);
  }
  p = put(line, "micdisc ");
  *p++ = tag;
  p = hexn(put(p, " ticks="), t0, 4);
  p = hexn(put(p, " st="), st, 1);
  p = hexn(put(p, " d="), disc, 1);
  p = hexn(put(p, " sel="), sel, 1);
  p = hexn(put(p, " 44="), api->fd_read(0x44), 4);
  p = dbm(put(p, " rssi="), rssi(api));
  *p = 0;
  api->say(line);
  api->delay_us(30000); /* the serial port drains before the next capture */
}

/* the 22 captures of one pass; tags from tags. 0, or -5 / -6 */
static int pass(const run_t *r, const char *tags, uint32_t r44, uint32_t r47, int *made) {
  const k6_api_t *api = r->api;
  uint32_t k;
  for (k = 0; k < N_CFG; k++) {
    uint32_t st = k == 0 || k == 2 ? 0u : k >= 19 ? 2u : 1u, disc = k >= 2, sel = k < 3 ? 1u : k < 19 ? k - 3 : k == 19 ? 1u : k == 20 ? 4u : 9u;
    if (api->key_poll()) return -6;
    if (mic_state(api, st == 2 ? MIC_BITS : st == 1 ? MIC_ADC : 0) < 0) return -5;
    api->fd_write(0x44, (uint16_t)(disc ? r44 ^ DISC : r44));
    api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (sel << 8)));
    api->delay_us(30000);
    capture(r, tags[k], st, disc, sel);
    ++*made;
  }
  api->fd_write(0x44, (uint16_t)r44);
  api->fd_write(0x47, (uint16_t)r47);
  return mic_state(api, 0) < 0 ? -5 : 0;
}

int k6_main(const k6_api_t *api) {
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           room = 0, t0, t1, up = 0;
  uint16_t r47 = api->fd_read(0x47), r44 = api->fd_read(0x44);
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  run_t r;
  char *line, *p;
  int ret = 0, e;
  r.api = api;
  r.line = line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  r.buf = (uint16_t *)(line + 64);
  r.m_in = moder & ~(3u << (2 * PA11_PIN));
  if (room < 2 * N + 72) return -8;
  p = hexn(put(line, "micdisc start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 44="), r44, 4);
  p = hexn(put(p, " 47="), r47, 4);
  p = hexn(put(p, " 7e="), api->fd_read(0x7E), 4);
  *p = 0;
  api->say(line);
  wr(MIC_GPIOB + MIC_BRR, AMP);
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
  if ((e = pass(&r, "0123456789abcdefghijkl", r44, r47, &ret)) < 0) {
    ret = e;
    goto out;
  }
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
      api->say("micdisc no sound");
      goto out;
    }
    api->delay_us(20000);
  }
  *dbm(put(line, "micdisc sound rssi="), rssi(api)) = 0;
  api->say(line);
  api->tune(home, wide);
  api->delay_us(300000);
  if ((e = pass(&r, "ABCDEFGHIJKLMNOPQRSTUV", r44, r47, &ret)) < 0) {
    ret = e;
    goto out;
  }
  api->tune(SOUND_HZ, wide);
  api->delay_us(300000);
  *dbm(put(line, "micdisc check rssi="), rssi(api)) = 0;
  api->say(line);
out:
  api->fd_write(0x44, r44);
  mic_state(api, 0);
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x44, r44);
  api->fd_write(0x47, r47);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  api->say("micdisc done");
  return ret;
}
