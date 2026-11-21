/* micaf: with the programming cable out, does the K6's mic reach the AF pin (PA11) in any state?
   (2026-11-21, Jeff; docs/k6-mic-probe.md). Every earlier AF-pin probe (micprobe, mickey) ran
   with the cable in the jack, which takes the mic out. micout (run mout1a) then showed with the
   cable out that REG_64, the mic level, follows a tone the IC-7100 plays at the mic. This asks
   the next question: is the mic's audio, not only its level, on one of REG_47's sixteen AF
   output selects, where PA11's converter (pa11adc.h) can read it?

   With the cable out nothing the K6 says reaches the logger, so each capture is scored on the
   radio and only the scores are kept (PA11 at hold 3, ~17.8 kHz; N_CAP samples after PRE):
     v  the capture's variance (sum of squares less the mean, of 0 .. 8100 samples)
     g  Goertzel power at TONE (1300 Hz, the tone played) and at three neighbours (1150, 1450,
        1700 Hz): a mic that hears the tone puts it at 1300 only, the converter's floor and
        the receiver's hiss everywhere
   36 configurations a pass: in receive with the mic ADC and the TX DSP on (REG_30 bits 2, 1;
   micadc.h), selects 0 .. 15; then keyed on the bench frequency with the PA off (the CW ID
   first, as mickey), selects 0 .. 15 with the mic ADC on and 1, 3, 4, 9 with it off.
   Three passes: q=0 quiet, q=1 once a carrier has stayed HOLD_MS on SOUND_HZ (the IC-7100 there
   plays the Pluto's tone at the mic, AF 0.3), q=2 after that carrier has dropped and come back
   (the job's second send, the IC-7100 at AF 0: the control, the same RF with no sound).

   The screen runs the bench, as micout: PULL CABLE / THEN ANY KEY, then the passes, then PLUG
   CABLE IN / THEN ANY KEY, and only then the lines:
     micaf start 30=bff1 47=6040 rx=1a46d650
     micaf pass q=1 rssi=-4c e=0              rssi on SOUND_HZ when it began (q=0: home)
     a 1 05 0e3a 0000000000012345 0000000000000678   q, configuration, ticks, v, g1300
     b 1 05 0000000000000123 0000000000000456 0000000000000789   g1150, g1450, g1700
     micaf done
   Configurations 00-0f receive (select = number), 10-1f keyed with the mic (select = number -
   0x10), 20-23 keyed without (selects 1, 3, 4, 9). The mic is never on the air: its bit is set
   keyed only while REG_36 says the PA is off. Writes, all put back: REG_30, REG_47, the tuning,
   PA11's mode, speed and output bit, PB2. Returns the passes made, or -8 (scratch too small),
   -7 (no key), -6 (a key mid-pass), -5 (the PA would not go off, or REG_30 did not take the
   bits), -4 (the bench frequency busy), -3 (tx_on refused), -2 (not on the bench frequency),
   -1 (no transmitter). Level 13.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 13
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "micadc.h"
#include "pa11adc.h"
#include "cableout.h"

#define AMP (1u << 2)
#ifndef SOUND_HZ
#define SOUND_HZ 445850000u
#endif
#ifndef BENCH_HZ
#define BENCH_HZ 440850000u
#endif
#ifndef N_CAP
#define N_CAP 1024u
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
#ifndef DROP_MS
#define DROP_MS 1000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 300000u
#endif
#ifndef KEY_WAIT_MS
#define KEY_WAIT_MS 1200000u
#endif
#define RSSI_ON -90 /* dBm: the sound's carrier is sent weak */
#define HOLD 3u
#define W 90u
#define N_RX 16u
#define N_TX 20u
#define N_CFG (N_RX + N_TX)
#define N_PASS 3u
#define N_F 4u
#define AF_DAC 0x0200u
#define CW_TEXT "AG7EW"
#define UNIT_US 60000u /* 20 wpm */
#define TX_MS 20000u

/* 2 cos(2 pi f / 17800) in Q14 for 1300, 1150, 1450, 1700 Hz */
static const int32_t COEF[N_F] = {29378, 30105, 28569, 27043};

typedef struct {
  uint64_t v, g[N_F];
  uint16_t ticks, cfg;
} res_t;

typedef struct {
  const k6_api_t *api;
  char *line; /* 64 bytes */
  uint16_t *buf;
  res_t *res;
  uint32_t m_in;
  uint16_t r47;
  int wide;
} run_t;

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

static char *hex64(char *p, uint64_t v) { return hexn(hexn(p, (uint32_t)(v >> 32), 8), (uint32_t)v, 8); }

static char *dbm(char *p, int v) {
  if (v < 0) {
    *p++ = '-';
    v = -v;
  }
  return hexn(p, (uint32_t)v, 2);
}

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }

/* Morse by code, as cwid.c: start at 1, then 0 for a dot and 1 for a dash (E = 2, A = 5) */
static int code_of(char c) {
  static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c) return i;
  return 0;
}

/* the PA on or off for n units; 0, -5 (the core keyed down) or -6 (a key) */
static int hold(const k6_api_t *api, int on, int n) {
  if (api->tx_pa(on) < 0) return -5;
  while (n--) {
    if (api->key_poll()) return -6;
    api->delay_us(UNIT_US);
  }
  return 0;
}

/* no runtime library: a 32 x 32 -> 64 multiply in 16-bit pieces, and c * s >> 14 (c < 2^15) in
   two halves so nothing overflows 32 bits */
static uint64_t umul(uint32_t a, uint32_t b) {
  uint32_t al = a & 0xFFFFu, ah = a >> 16, bl = b & 0xFFFFu, bh = b >> 16;
  uint64_t mid = (uint64_t)(al * bh) + (uint64_t)(ah * bl);
  return (uint64_t)(al * bl) + (mid << 16) + ((uint64_t)(ah * bh) << 32);
}

static int64_t smul(int32_t a, int32_t b) {
  uint64_t m = umul((uint32_t)(a < 0 ? -a : a), (uint32_t)(b < 0 ? -b : b));
  return (a < 0) != (b < 0) ? -(int64_t)m : (int64_t)m;
}

static int32_t mulq14(int32_t c, int32_t s) { return (s >> 14) * c + (((s & 0x3FFF) * c) >> 14); }

/* one capture at select sel, scored into *o */
static void capture(const run_t *r, uint32_t sel, uint32_t cfg, res_t *o) {
  const k6_api_t *api = r->api;
  pa11_t c = {0, 0, 0, 0};
  uint32_t i, k, t0 = 0, sum = 0;
  int32_t mean;
  api->fd_write(0x47, (uint16_t)((r->r47 & ~0x0F00u) | (sel << 8)));
  api->delay_us(20000);
  for (i = 0; i < PRE + N_CAP; i++) {
    uint32_t y;
    if (i == PRE) t0 = api->ticks();
    y = pa11_sample(r->m_in, HOLD, W, &c);
    if (i >= PRE) r->buf[i - PRE] = (uint16_t)y;
  }
  o->ticks = (uint16_t)(api->ticks() - t0);
  o->cfg = (uint16_t)cfg;
  for (i = 0; i < N_CAP; i++) sum += r->buf[i];
  mean = (int32_t)(sum / N_CAP);
  o->v = 0;
  for (i = 0; i < N_CAP; i++) {
    int32_t x = (int32_t)r->buf[i] - mean;
    o->v += (uint32_t)(x * x);
  }
  for (k = 0; k < N_F; k++) {
    int32_t s1 = 0, s2 = 0, s0;
    for (i = 0; i < N_CAP; i++) {
      s0 = ((int32_t)r->buf[i] - mean) + mulq14(COEF[k], s1) - s2;
      s2 = s1;
      s1 = s0;
    }
    o->g[k] = (uint64_t)(smul(s1, s1) + smul(s2, s2) - smul(mulq14(COEF[k], s1), s2));
  }
}

/* one pass: the receive sweep, then keyed with the PA off. 0 or a negative error (receive
   state and keying put back either way) */
static int pass(const run_t *r, res_t *o) {
  const k6_api_t *api = r->api;
  uint32_t k, s30 = 0;
  const char *s;
  int e = 0;
  if (mic_state(api, MIC_BITS) < 0) e = -5;
  for (k = 0; k < N_RX && !e; k++) {
    if (api->key_poll()) e = -6;
    else capture(r, k, k, o++);
  }
  api->fd_write(0x47, r->r47);
  if (mic_state(api, 0) < 0 && !e) e = -5;
  if (e) return e;
  if (rssi(api) >= RSSI_ON) return -4; /* never onto a busy channel */
  if (api->tx_on(TX_MS) != 0) return -3;
  e = hold(api, 0, 5);
  for (s = CW_TEXT; *s && !e; s++) {
    int c = code_of(*s), b;
    for (b = 31; !(c >> b); b--) {}
    while (b-- && !e) {
      e = hold(api, 1, (c >> b) & 1 ? 3 : 1);
      if (!e) e = hold(api, 0, b ? 1 : 3);
    }
  }
  if (!e && api->tx_pa(0) < 0) e = -5;
  if (!e) {
    s30 = api->fd_read(0x30);
    if (api->fd_read(0x36) & 0x0080u) e = -5; /* the PA is still on: no mic bit */
  }
  for (k = 0; k < N_TX && !e; k++) {
    uint32_t mic = k < 16, sel = mic ? k : k == 16 ? 1u : k == 17 ? 3u : k == 18 ? 4u : 9u,
             want = (s30 & ~MIC_ADC) | AF_DAC | (mic ? MIC_ADC : 0);
    if (api->key_poll()) e = -6;
    if (api->fd_read(0x36) & 0x0080u) e = -5;
    if (e) break;
    if (api->fd_read(0x30) != want && api->fd_write(0x30, (uint16_t)want) != 0) mic_bus_write(api, 0x30, want);
    if (api->fd_read(0x30) != want) {
      e = -5;
      break;
    }
    capture(r, sel, mic ? 0x10u + k : 0x20u + (k - 16), o++);
  }
  if (s30 && api->fd_read(0x30) != s30 && api->fd_write(0x30, (uint16_t)s30) != 0) mic_bus_write(api, 0x30, s30);
  api->fd_write(0x47, r->r47);
  api->tx_off();
  return e;
}

/* a carrier on SOUND_HZ that stays HOLD_MS, after (drop) one that has been gone DROP_MS:
   0 (tuned home again), -6 a key, -9 none within WAIT_MS */
static int sound(const run_t *r, int drop, int *dbm_at) {
  const k6_api_t *api = r->api;
  uint32_t t0, t1 = 0, up = 0, home = api->rx_hz();
  int e = 0;
  api->tune(SOUND_HZ, r->wide);
  t0 = api->ms();
  if (drop) {
    t1 = api->ms();
    for (;;) {
      if (rssi(api) >= RSSI_ON) t1 = api->ms();
      else if (api->ms() - t1 >= DROP_MS) break;
      if (api->key_poll()) e = -6;
      else if (api->ms() - t0 >= WAIT_MS) e = -9;
      if (e) goto out;
      api->delay_us(20000);
    }
  }
  for (;;) {
    if (rssi(api) < RSSI_ON) {
      up = 0;
    } else if (!up) {
      up = 1;
      t1 = api->ms();
    } else if (api->ms() - t1 >= HOLD_MS) {
      break;
    }
    if (api->key_poll()) e = -6;
    else if (api->ms() - t0 >= WAIT_MS) e = -9;
    if (e) goto out;
    api->delay_us(20000);
  }
  *dbm_at = rssi(api);
out:
  api->tune(home, r->wide);
  api->delay_us(300000);
  return e;
}

int k6_main(const k6_api_t *api) {
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           room = 0, t0, t1, q, k;
  int at[N_PASS], err[N_PASS], made[N_PASS];
  run_t r;
  char *line, *p;
  int ret = 0, e;
  if (api->level < 6) return -1;
  r.api = api;
  r.r47 = api->fd_read(0x47);
  r.wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  r.line = line = (char *)(((uint32_t)api->scratch(&room) + 7) & ~7u);
  r.res = (res_t *)(line + 64);
  r.buf = (uint16_t *)(r.res + N_PASS * N_CFG);
  r.m_in = moder & ~(3u << (2 * PA11_PIN));
  if (room < 64 + N_PASS * N_CFG * sizeof(res_t) + 2 * N_CAP + 8) return -8;
  p = hexn(put(line, "micaf start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 47="), r.r47, 4);
  p = hexn(put(p, " rx="), home, 8);
  *p = 0;
  api->say(line);
  if (home != BENCH_HZ) return -2;
  for (q = 0; q < N_PASS; q++) at[q] = err[q] = made[q] = 0;
  wr(MIC_GPIOB + MIC_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  co_show(api, "MICAF CABLE OUT", "PULL CABLE", "THEN ANY KEY");
  if (co_key(api, KEY_WAIT_MS) < 0) {
    ret = -7;
    goto out;
  }
  co_show(api, "MICAF CABLE OUT", "QUIET PLEASE", "PASS 0");
  /* the upload's tail is long gone by the key, but wait for a quiet second all the same */
  t0 = api->ms();
  t1 = t0;
  while (api->ms() - t0 < QUIET_WAIT_MS && api->ms() - t1 < 1000u) {
    if (rssi(api) >= RSSI_ON) t1 = api->ms();
    api->delay_us(20000);
  }
  at[0] = rssi(api);
  for (q = 0; q < N_PASS; q++) {
    res_t *o = r.res + q * N_CFG;
    if (q) {
      co_show(api, "MICAF CABLE OUT", "QUIET PLEASE", q == 1 ? "PASS 1 SOUND" : "PASS 2 CONTROL");
      e = sound(&r, q == 2, &at[q]);
      if (e) {
        err[q] = e;
        break;
      }
    }
    for (k = 0; k < N_CFG; k++) o[k].cfg = 0xFFFFu;
    e = pass(&r, o);
    err[q] = e;
    for (k = 0; k < N_CFG && o[k].cfg != 0xFFFFu; k++) {}
    made[q] = (int)k;
    if (e) break;
    ret++;
  }
out:
  api->tx_off();
  api->tune(home, r.wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r.r47);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  if (ret != -7) {
    co_show(api, "MICAF CABLE OUT", "PLUG CABLE IN", "THEN ANY KEY");
    co_key(api, KEY_WAIT_MS);
    co_show(api, "MICAF CABLE OUT", "SENDING", "");
    api->delay_us(1000000);
  }
  for (q = 0; q < N_PASS && ret != -7; q++) {
    res_t *o = r.res + q * N_CFG;
    p = hexn(put(line, "micaf pass q="), q, 1);
    p = dbm(put(p, " rssi="), at[q]);
    p = put(p, " e=");
    if (err[q] < 0) *p++ = '-';
    p = hexn(p, (uint32_t)(err[q] < 0 ? -err[q] : err[q]), 1);
    *p = 0;
    api->say(line);
    for (k = 0; k < (uint32_t)made[q]; k++) {
      p = hexn(put(line, "a "), q, 1);
      p = hexn(put(p, " "), o[k].cfg, 2);
      p = hexn(put(p, " "), o[k].ticks, 4);
      p = hex64(put(p, " "), o[k].v);
      p = hex64(put(p, " "), o[k].g[0]);
      *p = 0;
      api->say(line);
      p = hexn(put(line, "b "), q, 1);
      p = hexn(put(p, " "), o[k].cfg, 2);
      p = hex64(put(p, " "), o[k].g[1]);
      p = hex64(put(p, " "), o[k].g[2]);
      p = hex64(put(p, " "), o[k].g[3]);
      *p = 0;
      api->say(line);
      api->delay_us(15000); /* the serial port drains */
    }
  }
  api->say("micaf done");
  return ret;
}
