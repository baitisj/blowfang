/* miclvl: does the "mic route"'s level follow the sound? (2026-11-20, docs/k6-mic-probe.md)
   (It does not: these runs showed the route to be the receiver's raw baseband, and the sound
   in it the bench's Pluto leaking in by radio.)
   micrec's voice came out no louder with the speaker an inch from the mic than a foot away.
   This looks at the route (micadc.h's mic ADC and TX DSP on in receive, REG_47 select 4, PA11's
   converter) over 48 seconds while another radio plays a tone at the mic in steps of loudness:

     0  1,495 samples, the room quiet, before the sound
   then, once a carrier has stayed 5 s on SOUND_HZ, back on the K6's own channel, one capture a
   second (tags 1..9, a..z, A..Z in turn), four kinds in rotation:
     st=2  the route as micrec uses it
     st=1  the mic ADC off (the TX DSP alone): what is left is not the mic's
     st=2  again
     st=3  REG_19 bit 15 the other way (the chip's mic AGC: 0x1041 on the radio has it on)
   With SOUND_HZ a channel no radio in the room listens to, the same run asks whether the sound
   reaches select 4 by radio.

   Lines (under 60 characters; samples are four hex digits, 13 a line):
     miclvl start 30=bff1 48=82df 19=1041 7d=e95c
     0 000 11f811e011f0...
     miclvl 0 ticks=0349 ms=00000 st=2 19=1041 rssi=-66
     miclvl sound rssi=-3d            (or: miclvl no sound)
     miclvl check rssi=-3d
     miclvl done
   ms counts from the first capture after the sound. Writes, all put back: REG_30 (bits 2, 1,
   by micadc.h's rule), REG_47 (the select), REG_19 (bit 15), the tuning, PA11's mode, speed and
   output bit, PB2. Returns the captures made, or -8 (scratch under 3.1 KB), -6 (a key), -5
   (REG_30 would not take the bits, or the chip is not in receive with the PA off). Level 4.
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
#define N 1495u /* 115 lines of 13 */
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
#ifndef RUN_MS
#define RUN_MS 48000u
#endif
#define RSSI_ON -85 /* dBm */
#define SEL 4u
#define HOLD 3u
#define W 90u
#define AGC_OFF 0x8000u

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
static void capture(const run_t *r, char tag, uint32_t st, uint32_t ms) {
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
  p = put(line, "miclvl ");
  *p++ = tag;
  p = hexn(put(p, " ticks="), t0, 4);
  p = hexn(put(p, " ms="), ms, 5);
  p = hexn(put(p, " st="), st, 1);
  p = hexn(put(p, " 19="), api->fd_read(0x19), 4);
  p = dbm(put(p, " rssi="), rssi(api));
  *p = 0;
  api->say(line);
  api->delay_us(50000); /* the serial port drains before the next capture */
}

int k6_main(const k6_api_t *api) {
  static const char TAGS[] = "123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           room = 0, t0, t1, up = 0, k;
  uint16_t r47 = api->fd_read(0x47), r19 = api->fd_read(0x19);
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  run_t r;
  char *line, *p;
  int ret = 0;
  r.api = api;
  r.line = line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  r.buf = (uint16_t *)(line + 64);
  r.m_in = moder & ~(3u << (2 * PA11_PIN));
  if (room < 2 * N + 72) return -8;
  p = hexn(put(line, "miclvl start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 48="), api->fd_read(0x48), 4);
  p = hexn(put(p, " 19="), r19, 4);
  p = hexn(put(p, " 7d="), api->fd_read(0x7D), 4);
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
  api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (SEL << 8)));
  api->delay_us(50000);
  capture(&r, '0', 2, 0);
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
      api->say("miclvl no sound");
      goto out;
    }
    api->delay_us(20000);
  }
  *dbm(put(line, "miclvl sound rssi="), rssi(api)) = 0;
  api->say(line);
  api->tune(home, wide);
  api->delay_us(300000);
  t0 = api->ms();
  for (k = 0; TAGS[k] && api->ms() - t0 < RUN_MS; k++) {
    uint32_t st = (k & 3u) == 1 ? 1u : (k & 3u) == 3 ? 3u : 2u;
    if (api->key_poll()) break;
    if (mic_state(api, st == 1 ? MIC_DSP : MIC_BITS) < 0) {
      ret = -5;
      goto out;
    }
    api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (SEL << 8)));
    api->fd_write(0x19, (uint16_t)(st == 3 ? r19 ^ AGC_OFF : r19));
    api->delay_us(50000);
    capture(&r, TAGS[k], st, api->ms() - t0);
    ret++;
  }
  api->fd_write(0x19, r19);
  mic_state(api, 0);
  api->tune(SOUND_HZ, wide);
  api->delay_us(300000);
  *dbm(put(line, "miclvl check rssi="), rssi(api)) = 0;
  api->say(line);
out:
  mic_state(api, 0);
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r47);
  api->fd_write(0x19, r19);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  api->say("miclvl done");
  return ret;
}
