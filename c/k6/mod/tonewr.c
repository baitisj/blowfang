/* tonewr: what a write does to the FD6818's tone oscillator's phase, on air with the PA off
   (module API level 3), for the range sender (rangetx). K7 and K8 (2026-11-13): the K6's PHY R
   burst read 0 of 6 on the UV-380, and its audio has a runt half cycle at every 600 -> 1200 Hz
   change: the phase jumps there. This sends, from tone-on (t = 0, every write timed on TIM17 to
   land on the schedule, its own length early, as rangetx does):

     A  0.0-1.0 s   600 Hz, untouched: the reference
     B  1.0-2.0 s   600 Hz, REG_71 rewritten with the same word every 7.3 ms (137 writes): does
                    a write that changes nothing move the phase?
     C  2.0-4.0 s   REG_71 600 / 1200 Hz in turn, steps of 37.1 ms plus 0.1-1.6 ms (54 changes,
                    landing at every phase): is the phase continuous across a change, or set to
                    some phase at the write?
     D  4.0-5.0 s   600 Hz, REG_70's tone 1 off for 37.1 ms, on for 74.2 ms, 9 times:
                    does the oscillator keep running while off, or start again at a phase?
     E  5.0-7.0 s   tone 2 (REG_72 = 1200 Hz, written before the key-up, REG_70 bits 7 and 6:0)
                    and tone 1 (600 Hz) gated in turn every 37.1 ms (53 swaps): is there a tone
                    2, and do two free-running tones give a phase-continuous FFSK?
     tone off at 7.0 s, 400 ms, then AG7EW in MCW, and key down.

   It keys up only on 440.850 after the channel has been clear for 1 s, PA off from just after
   tx_on (only the chip's leak, as rangetx_lo), and puts REG_72 and TIM17 back. Record it on the
   IC-7100 (FM-D, 48 kHz) or the UV-380 (rxrec); tools/k6_tonewr.py report reads the recording.
   It says
     K6 MOD say tonewr pa=0 g=60 r72=xxxx rx=440850000
     K6 MOD say tonewr w=397 late=0 wr=11223-11232 d=99      writes, late starts, a write's length
                                                              and the worst landing error (cycles)
     K6 MOD say tonewr sent ms=7000 id=52
   and returns the writes made, or rangetx's errors (-2 not on 440.850, -3 tx_on refused, -4 a
   key, -5 time up, -7 busy channel, -8 a write too slow). SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"

#ifndef TW_HZ
#define TW_HZ 440850000u
#endif
#ifndef TW_GAIN
#define TW_GAIN 60
#endif
#ifndef TW_PA
#define TW_PA 0
#endif
#define ID_TEXT "AG7EW"
#define ID_WPM 20
#define ID_HZ 800

#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT 0x40002024u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define TIM17EN (1u << 18)

#define MS 96000u                                    /* cycles a ms */
#define W600 6195u
#define W1200 12389u
#define T1 ((uint16_t)(0x8000u | ((TW_GAIN & 0x7Fu) << 8)))
#define T2 ((uint16_t)(0x0080u | (TW_GAIN & 0x7Fu)))
#define STEP_B (73u * MS / 10u)                      /* 7.3 ms */
#define STEP (371u * MS / 10u)                       /* 37.1 ms */
#define JIT (MS / 10u)                               /* 100 us */
#define WR_MAX (5u * MS)
#define WORD(hz) ((uint16_t)((uint32_t)(hz) * 1032444u / 100000u))
#define UNIT_MS (1200u / ID_WPM)
#define ID_UNITS 52
#define TX_MS (300u + 7000u + 400u + ID_UNITS * UNIT_MS + 1500u)

static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }

typedef struct {
  uint32_t t, c17, c14, apb2;
  uint32_t len, w, late, wmin, wmax, dmax, tkey, chk;
} st_t;

static uint32_t now(st_t *c) {
  uint32_t c17 = rd(TIM17_CNT) & 0xFFFF, c14 = rd(TIM14_CNT) & 0xFFFF;
  uint32_t coarse = ((c14 - c->c14) & 0xFFFF) * 9600u, fine = (c17 - c->c17) & 0xFFFF;
  int32_t d = (int32_t)(int16_t)(uint16_t)(fine - coarse);
  c->c17 = c17, c->c14 = c14;
  c->t += coarse + (uint32_t)d;
  return c->t;
}

static void clk_on(st_t *c) {
  c->apb2 = rd(RCC_APB2ENR);
  wr(RCC_APB2ENR, c->apb2 | TIM17EN);
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x28, 0);
  wr(TIM17 + 0x2C, 0xFFFF);
  wr(TIM17 + 0x14, 1);
  wr(TIM17 + 0x00, 1);
  c->t = 0;
  c->c17 = rd(TIM17_CNT) & 0xFFFF, c->c14 = rd(TIM14_CNT) & 0xFFFF;
}

static void clk_off(st_t *c) {
  wr(TIM17 + 0x00, 0);
  wr(RCC_APB2ENR, c->apb2);
}

static void wait_to(const k6_api_t *api, st_t *c, uint32_t t) {
  int32_t rem;
  while ((rem = (int32_t)(t - now(c))) > 96 * 60) {
    uint32_t us = (((uint32_t)rem * 683u) >> 16) - 30u;
    api->delay_us(us > 200u ? 200u : us);
  }
  while ((int32_t)(t - now(c)) > 0) {}
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {
  static const uint32_t pw[] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u,
                                10000u, 1000u, 100u, 10u, 1u};
  int i, started = 0;
  for (i = 0; i < 10; i++) {
    char d = '0';
    while (v >= pw[i]) { v -= pw[i]; d++; }
    if (d != '0' || started || i == 9) { *p++ = d; started = 1; }
  }
  return p;
}

static char *hex4(char *p, uint32_t v) {
  int k;
  for (k = 12; k >= 0; k -= 4) {
    uint32_t d = (v >> k) & 15u;
    *p++ = (char)(d < 10u ? '0' + d : 'a' + d - 10u);
  }
  return p;
}

static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";

static int code_of(char c) {
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c && c != '~') return i;
  return 0;
}

static int until(const k6_api_t *api, uint32_t t) {
  if (api->key_down()) return -4;
  while ((int32_t)(api->ms() - t) < 0) api->delay_us(250);
  return 0;
}

static int keyed(const k6_api_t *api) {
  return api->tx_pa(TW_PA) < 0 ? -5 : 0;
}

static int mcw(const k6_api_t *api) {
  const char *s;
  int units = 0, r = 0;
  uint32_t t = api->ms();
  api->fd_write(0x70, 0);
  api->fd_write(0x71, WORD(ID_HZ));
  for (s = ID_TEXT; *s && !r; s++) {
    int c = code_of(*s), b;
    for (b = 31; !(c >> b); b--) {}
    while (b-- && !r) {
      int n = (c >> b) & 1 ? 3 : 1, g = b ? 1 : 3;
      if ((r = keyed(api)) != 0) break;
      api->fd_write(0x70, T1);
      t += (uint32_t)n * UNIT_MS;
      if ((r = until(api, t)) != 0) break;
      api->fd_write(0x70, 0);
      t += (uint32_t)g * UNIT_MS;
      r = until(api, t);
      units += n + g;
    }
  }
  api->fd_write(0x70, 0);
  return r ? r : units;
}

/* a key check or the keep-alive, in turn, every 50 ms: 0, -4 or -5 */
static int check(const k6_api_t *api, st_t *c, uint32_t t) {
  if (t - c->tkey <= 50u * MS) return 0;
  c->tkey = t;
  if (c->chk++ & 1u)
    return api->key_down() ? -4 : 0;
  api->ms();
  return keyed(api);
}

/* write reg = v so that the write ends at cycle t, checking keys and the core while it waits */
static int wr_at(const k6_api_t *api, st_t *c, uint32_t t, uint8_t reg, uint16_t v) {
  uint32_t t0, t1, e;
  int32_t d;
  int r;
  if ((int32_t)(t - c->len - now(c)) < 0) c->late++;
  while ((int32_t)(t - c->len - now(c)) > (int32_t)(3u * MS))
    if ((r = check(api, c, c->t)) != 0) return r;
    else api->delay_us(500);
  wait_to(api, c, t - c->len);
  t0 = now(c);
  api->fd_write(reg, v);
  t1 = now(c);
  c->len = t1 - t0;
  if (c->len < c->wmin) c->wmin = c->len;
  if (c->len > c->wmax) c->wmax = c->len;
  d = (int32_t)(t1 - t);
  e = (uint32_t)(d < 0 ? -d : d);
  if (e > c->dmax) c->dmax = e;
  c->w++;
  return 0;
}

/* write n of C, D or E lands JIT x j(n) after n steps, j(0) = 1, j(n + 1) = 3 j(n) mod 17 (1-16
   in a scrambled order): the steps' lengths vary by up to 1.5 ms (0.9 of a 600 Hz cycle), so a
   write that sets the phase shows as a phase before it that varies (with steps all alike, both
   kinds of oscillator would look the same) */
static uint32_t next_j(uint32_t j) {
  j *= 3u;                                   /* (j < 17, so 3 j < 51: two compares, no divide) */
  return j >= 34u ? j - 34u : j >= 17u ? j - 17u : j;
}

static int run(const k6_api_t *api, st_t *c) {
  uint32_t t, t0, k, j, m;
  int r = 0;
  /* the write's length, from writes that change nothing (the tone still off) */
  c->len = 0;
  for (k = 0; k < 3; k++) {
    t = now(c);
    api->fd_write(0x71, W600);
    t = now(c) - t;
    if (t > c->len) c->len = t;
  }
  c->wmin = c->wmax = c->len;
  if (c->len > WR_MAX) return -8;
  api->fd_write(0x70, T1);
  t0 = c->tkey = now(c);
  /* B: the same word every 7.3 ms */
  for (k = 0; !r && k < 137u; k++)
    r = wr_at(api, c, t0 + 1000u * MS + k * STEP_B, 0x71, W600);
  /* C: 1200 / 600 in turn */
  for (k = 0, j = 1; !r && k < 54u; k++, j = next_j(j))
    r = wr_at(api, c, t0 + 2000u * MS + k * STEP + j * JIT, 0x71, (k & 1u) ? W600 : W1200);
  /* D: tone 1 off for one step, on for two (an on shorter than the ID's 60 ms dot would read as
     the ID's unit to anyone timing the keying) */
  for (k = 0, j = 1, m = 0; !r && k < 27u; k++, j = next_j(j), m = m == 2u ? 0 : m + 1u)
    if (m != 2u)                                     /* (m = k mod 3: the M0 has no divide) */
      r = wr_at(api, c, t0 + 4000u * MS + k * STEP + j * JIT, 0x70, m ? T1 : 0);
  /* E: tone 2 / tone 1 */
  for (k = 0, j = 1; !r && k < 53u; k++, j = next_j(j))
    r = wr_at(api, c, t0 + 5000u * MS + k * STEP + j * JIT, 0x70, (k & 1u) ? T1 : T2);
  if (!r) r = wr_at(api, c, t0 + 7000u * MS, 0x70, 0);
  return r;
}

int k6_main(const k6_api_t *api) {
  char line[64], *p;
  st_t c;
  uint32_t t;
  uint16_t r72;
  int r = 0, id = 0;
  c.w = c.late = c.wmin = c.wmax = c.dmax = c.len = c.tkey = c.chk = 0;
  if (api->level < 3) return -1;
  r72 = api->fd_read(0x72);
  p = put(line, "tonewr pa=");
  p = dec(p, TW_PA);
  p = put(p, " g=");
  p = dec(p, TW_GAIN);
  p = put(p, " r72=");
  p = hex4(p, r72);
  p = put(p, " rx=");
  p = dec(p, api->rx_hz());
  *p = 0;
  api->say(line);
  if (api->rx_hz() != TW_HZ) return -2;
  {
    uint32_t clear = api->ms();
    while (api->ms() - clear < 1000u) {
      if (KM_RSSI_DBM(api->fd_read(0x67)) >= -90) clear = api->ms();
      if (api->ms() > 30000u) return -7;
      if (api->key_down()) return -4;
      api->delay_us(10000);
    }
  }
  api->fd_write(0x72, W1200);                 /* tone 2's frequency, before the key-up */
  api->delay_us(1000);
  if (api->tx_on(TX_MS) != 0) {
    api->fd_write(0x72, r72);
    return -3;
  }
  if (!TW_PA) api->tx_pa(0);
  api->fd_write(0x70, 0);
  api->fd_write(0x71, W600);
  clk_on(&c);
  t = api->ms();
  r = until(api, t + 300u);
  if (!r) r = run(api, &c);
  clk_off(&c);
  api->fd_write(0x70, 0);
  t = api->ms() - t - 300u;
  p = put(line, "tonewr w=");
  p = dec(p, c.w);
  p = put(p, " late=");
  p = dec(p, c.late);
  p = put(p, " wr=");
  p = dec(p, c.wmin);
  p = put(p, "-");
  p = dec(p, c.wmax);
  p = put(p, " d=");
  p = dec(p, c.dmax);
  *p = 0;
  api->say(line);
  if (!r || r == -8) {
    until(api, api->ms() + 400u);
    id = mcw(api);
    if (id < 0) r = id;
  }
  api->fd_write(0x70, 0);
  api->fd_write(0x71, 0);
  api->tx_off();
  api->delay_us(1000);                        /* keyed down, then REG_72 back */
  api->fd_write(0x72, r72);
  if (r) return r;
  p = put(line, "tonewr sent ms=");
  p = dec(p, t);
  p = put(p, " id=");
  p = dec(p, (uint32_t)id);
  *p = 0;
  api->say(line);
  return (int)c.w;
}
