/* micpin: micprobe's one hit, AF select 4 with the mic ADC and TX DSP on, in whole captures
   (2026-11-20, docs/k6-mic-probe.md). It was read as the K6's mic. It is the receiver's raw
   baseband, and the tone in it was the bench's Pluto leaking in by radio (the write-up's "The
   correction"). The captures: with the FD6818's mic ADC and TX DSP switched on while the K6
   receives (micadc.h; the PA off, nothing keyed), REG_47's AF output select 4 on the AF pin,
   read by PA11's converter (pa11adc.h).

   Captures, each dumped whole as four hex digits a sample for tools/k6_micprobe.py:
     Q  2,990 samples, select 4, the room quiet: the floor
   then, once a carrier has stayed 8 s on 445.850 MHz (another radio there plays the bench's
   tone at the K6's mic), back on the K6's own channel:
     a  2,990, select 4, the gains as found
     b  2,990, REG_48's AF gain 2 at its most (+9 dB or so)
     c  2,990, and REG_7D's mic sensitivity at its most
     d  2,990, as c with drive time 2
     e  2,990, as c on select 5 (the other "baseband" select)
     f  2,990, as c on select 4 with only the mic ADC on (the TX DSP off again)
     L  as c, twice the turns a sample (about 9 kHz), as many samples as the scratch holds
        (0.7 s): the one to listen to
   Lines (under 60 characters):
     micpin start 30=bff1 47=6040 48=82df 7d=e95c
     Q 000 11f811e011f0...            tag, first sample's index (hex), 13 samples
     micpin Q ticks=0690 way=1 hold=3 w=5a rssi=-72 sel=4 48=82df
     micpin sound rssi=-3d            (or: micpin no sound)
     micpin check rssi=-3d            445.850 after the captures: the tone was still there
     micpin done
   Writes, all put back: REG_30 (bits 2, 1, by micadc.h's rule), REG_47 (the select), REG_48,
   REG_7D, the tuning, PA11's mode, speed and output bit, PB2. Returns the captures made, or
   -8 (scratch under 6 KB), -6 (a key), -5 (REG_30 would not take the bits, or the chip is not
   in receive with the PA off). Level 4.
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
#define N 2990u /* 230 lines of 13 */
#endif
#ifndef N_LONG
#define N_LONG 6500u
#endif
#ifndef PRE
#define PRE 2400u /* samples before the ones kept: the 1 uF coupling capacitor settles */
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
  uint32_t m_in, r47;
} run_t;

/* one capture and its dump: n samples at REG_47 select sel, drive time hold, w turns a sample */
static void capture(const run_t *r, char tag, uint32_t sel, uint32_t hold, uint32_t w, uint32_t n) {
  const k6_api_t *api = r->api;
  char *line = r->line, *p;
  uint32_t i, j, t0 = 0;
  pa11_t c = {0, 0, 0, 0};
  api->fd_write(0x47, (uint16_t)((r->r47 & ~0x0F00u) | (sel << 8)));
  for (i = 0; i < PRE + n; i++) {
    uint32_t y;
    if (i == PRE) t0 = api->ticks();
    y = pa11_sample(r->m_in, hold, w, &c);
    if (i >= PRE) r->buf[i - PRE] = (uint16_t)y;
  }
  t0 = (api->ticks() - t0) & 0xFFFFu;
  for (i = 0; i < n; i += 13) {
    line[0] = tag;
    line[1] = ' ';
    p = hexn(line + 2, i, 3);
    *p++ = ' ';
    for (j = i; j < i + 13 && j < n; j++) p = hexn(p, r->buf[j], 4);
    *p = 0;
    api->say(line);
  }
  p = put(line, "micpin ");
  *p++ = tag;
  p = hexn(put(p, " ticks="), t0, 4);
  p = hexn(put(p, " way=1 hold="), hold, 1);
  p = hexn(put(p, " w="), w, 2);
  p = dbm(put(p, " rssi="), rssi(api));
  p = hexn(put(p, " sel="), sel, 1);
  p = hexn(put(p, " 48="), api->fd_read(0x48), 4);
  *p = 0;
  api->say(line);
  api->delay_us(50000); /* the serial port drains before the next capture */
}

int k6_main(const k6_api_t *api) {
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           room = 0, t0, t1, up = 0, n_long;
  uint16_t r47 = api->fd_read(0x47), r48 = api->fd_read(0x48), r7d = api->fd_read(0x7D);
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  run_t r;
  char *line, *p;
  int ret = 0;
  r.api = api;
  r.line = line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  r.buf = (uint16_t *)(line + 64);
  r.m_in = moder & ~(3u << (2 * PA11_PIN));
  r.r47 = r47;
  if (room < 2 * N + 72) return -8;
  n_long = (room - 72) / 2;
  if (n_long > N_LONG) n_long = N_LONG;
  n_long -= n_long % 13u;
  p = hexn(put(line, "micpin start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 47="), r47, 4);
  p = hexn(put(p, " 48="), r48, 4);
  p = hexn(put(p, " 7d="), r7d, 4);
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
  if (mic_state(api, MIC_BITS) < 0) {
    ret = -5;
    goto out;
  }
  api->delay_us(50000);
  capture(&r, 'Q', 4, 3, 90, N);
  ret++;
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
      api->say("micpin no sound");
      goto out;
    }
    api->delay_us(20000);
  }
  *dbm(put(line, "micpin sound rssi="), rssi(api)) = 0;
  api->say(line);
  api->tune(home, wide);
  api->delay_us(300000);
  if (mic_state(api, MIC_BITS) < 0) {
    ret = -5;
    goto out;
  }
  api->delay_us(50000);
  capture(&r, 'a', 4, 3, 90, N);
  api->fd_write(0x48, (uint16_t)(r48 | 0x03F0u));
  capture(&r, 'b', 4, 3, 90, N);
  api->fd_write(0x7D, (uint16_t)(r7d | 0x001Fu));
  capture(&r, 'c', 4, 3, 90, N);
  capture(&r, 'd', 4, 2, 90, N);
  capture(&r, 'e', 5, 3, 90, N);
  ret += 5;
  if (mic_state(api, MIC_ADC) >= 0) {
    api->delay_us(50000);
    capture(&r, 'f', 4, 3, 90, N);
    ret++;
  }
  if (mic_state(api, MIC_BITS) >= 0 && !api->key_poll()) {
    api->delay_us(50000);
    capture(&r, 'L', 4, 3, 180, n_long);
    ret++;
  }
  mic_state(api, 0);
  api->tune(SOUND_HZ, wide);
  api->delay_us(300000);
  *dbm(put(line, "micpin check rssi="), rssi(api)) = 0;
  api->say(line);
out:
  mic_state(api, 0);
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r47);
  api->fd_write(0x48, r48);
  api->fd_write(0x7D, r7d);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  api->say("micpin done");
  return ret;
}
