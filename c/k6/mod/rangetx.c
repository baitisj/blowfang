/* rangetx: the K6 sends a PHY R burst itself (docs/k6-range.md), as FFSK from the FD6818's tone
   oscillator on FM, then identifies in MCW (module API level 3). The UV-380's rangerx (and the
   IC-7100 with modem.k6r) reads it.

   The burst is rangetx_bits.h (python -m modem.k6r txhdr): six test packets, the same as the
   bench wav's bursts, as tone bits (1: 600 Hz, 0: 1200 Hz) at 1200 baud. The module:

   - keys up only when the pager listens on RT_HZ (440.850 MHz, the bench frequency), after the
     channel has been clear for RT_CLEAR_MS (the upload's sender has unkeyed);
   - calls tx_on for the lead, the burst, the ID and a margin (about 17 s);
   - times the bits on the MCU's TIM17 at 96 MHz, unwrapped by Aura's 100 us TIM14 (as dmrdet):
     a bit is 80000 cycles. The tone oscillator (REG_70 enable | RT_GAIN << 8, REG_71 the
     frequency word, f x 10.32444) is phase continuous, so what the receiver needs is the tone's
     phase at each bit edge: a half cycle of 600 Hz or a whole one of 1200 Hz a bit. A tone
     change that lands late by d (cycles) leaves the phase off by 600 Hz x d, either way up for
     a change up or down, and a word that isn't exactly 600 or 1200 Hz (6195 is 600.033 Hz,
     12389 is 1199.968) moves it a little every bit. The module keeps that error as A (cycles at
     600 Hz, 1/256) and times each change to cancel it, so the phase stays within a write's
     jitter of where it should be. RT_PPM: the FD6818's crystal against the MCU's, if known;
   - writes REG_71 only where the tone changes, starting each write its own length early (the
     last write's, measured: a register write takes about 100-400 us), and REG_70 right after it
     for the new tone's gain (RT_GAIN 600 Hz, RT_GAIN12 1200 Hz: pre-emphasis, as an FM data
     sender's; the UV-380's de-emphasis takes the 1200 Hz tone about 6 dB down otherwise);
   - RT_PA 0 (rangetx_lo.c) turns the PA off right after tx_on and keeps it off: the lowest
     power the K6 has, only the chip's leak, for the bench;
   - after the burst, tone off, then AG7EW in MCW (the 800 Hz tone keyed with REG_70) at 20 wpm,
     and keys down. TIM17 and its clock enable are put back on every way out.

   It says
     K6 MOD say rangetx n=6 bits=13848 g=44/110 pa=1 rx=440850000
     K6 MOD say rangetx w=6912 late=0 wr=9000-9100 d=12 a=40
                                 tone changes; writes started late; a write's length (cycles at
                                 96 MHz, min-max); the worst error left at a change and |A| (cycles)
     K6 MOD say rangetx sent bits=13848 ms=11540 id=52
   and returns the packets sent, or a negative error:
     -1  the image has no level-3 transmitter      -2  not on RT_HZ
     -3  tx_on refused                             -4  a key was pressed (keyed down at once)
     -5  the core keyed down partway (time up)      -7  the channel stayed busy (RT_WAIT_MS)
     -8  a register write takes too long for 1200 baud (it IDs and keys down)

   Build: python -m modem.k6 mod out.wav c/k6/mod/rangetx.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "rangetx_bits.h"   /* RT_PACKETS, RT_NBITS, RT_TONES */
#ifndef K6_NAME
#define K6_NAME "RANGE TX"   /* its name in the MENU list (a file that includes this one may set its own) */
#endif
K6_MODULE_NAME(K6_NAME);

#ifndef RT_HZ
#define RT_HZ 440850000u
#endif
#ifndef RT_GAIN
#define RT_GAIN 44          /* REG_70 tone-1 gain for the 600 Hz bits, 0-127 (Aura's tone, ft8tx: 60) */
#endif
#ifndef RT_GAIN12
#define RT_GAIN12 110       /* and for the 1200 Hz bits: 2.5 times, the FM pre-emphasis a receiver's
                               de-emphasis expects (the UV-380 takes the 1200 Hz tone about 6 dB
                               under the 600 Hz one; RT_GAIN to send both alike) */
#endif
#ifndef ID_GAIN
#define ID_GAIN 60
#endif
#ifndef RT_PPM
#define RT_PPM 0.0          /* the FD6818's crystal against the MCU's, ppm (its tone fast: +) */
#endif
#ifndef RT_PA
#define RT_PA 1             /* 0: the PA off while keyed (rangetx_lo): only the chip's leak goes
                               out, about 40 dB down at bench range (k6-own-fsk.md) */
#endif
#ifndef RT_LEAD_MS
#define RT_LEAD_MS 300      /* carrier, tone off, before the burst: the PLL settles */
#endif
#ifndef RT_CLEAR_DBM
#define RT_CLEAR_DBM -90
#endif
#ifndef RT_CLEAR_MS
#define RT_CLEAR_MS 1000
#endif
#ifndef RT_WAIT_MS
#define RT_WAIT_MS 30000
#endif
#ifndef ID_TEXT
#define ID_TEXT "AG7EW"
#endif
#ifndef ID_WPM
#define ID_WPM 20
#endif
#ifndef ID_HZ
#define ID_HZ 800
#endif

#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT 0x40002024u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define TIM17EN (1u << 18)

#define BIT 80000u                                   /* cycles a bit: 96 MHz / 1200 */
#define W600 6195u                                   /* REG_71: 600.033 Hz */
#define W1200 12389u                                 /* 1199.968 Hz */
#define FHZ(w) ((double)(w) * 100000.0 / 1032444.0 * (1.0 + (RT_PPM) * 1e-6))
/* A's change over a bit at each tone (cycles at 600 Hz, 1/256): the phase it gains, in cycles */
#define DA600 ((int32_t)((FHZ(W600) / 600.0 - 1.0) * (double)BIT * 256.0))
#define DA1200 ((int32_t)((FHZ(W1200) - 1200.0) / 600.0 * (double)BIT * 256.0))
#define OFF_MAX 9600                                 /* a change moves at most 100 us */
#define WR_MAX (BIT * 3u / 4u)                       /* a write longer than this can't keep up */
#define KEY_EVERY 4800000u                           /* cycles between key checks: 50 ms */
#define REG70(g) ((uint16_t)(0x8000u | (((g) & 0x7Fu) << 8)))
#define REG70_ON REG70(ID_GAIN)
#define TWO (RT_GAIN12 != RT_GAIN)                   /* a REG_70 write with each change too */
#define WORD(hz) ((uint16_t)((uint32_t)(hz) * 1032444u / 100000u))
#define UNIT_MS (1200u / ID_WPM)
#define ID_UNITS 52                                  /* AG7EW: A 8, G 12, 7 16, E 4, W 12 */
#define BURST_MS (RT_NBITS * 5u / 6u)
#define TX_MS (RT_LEAD_MS + BURST_MS + 400u + ID_UNITS * UNIT_MS + 1500u)

static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }

typedef struct {
  uint32_t t, c17, c14;     /* 96 MHz cycles, unwrapped from TIM17 and TIM14 */
  uint32_t apb2;
} clk_t;

static uint32_t now(clk_t *c) {
  uint32_t c17 = rd(TIM17_CNT) & 0xFFFF, c14 = rd(TIM14_CNT) & 0xFFFF;
  uint32_t coarse = ((c14 - c->c14) & 0xFFFF) * 9600u, fine = (c17 - c->c17) & 0xFFFF;
  int32_t d = (int32_t)(int16_t)(uint16_t)(fine - coarse);
  c->c17 = c17, c->c14 = c14;
  c->t += coarse + (uint32_t)d;
  return c->t;
}

static void clk_on(clk_t *c) {
  c->apb2 = rd(RCC_APB2ENR);
  wr(RCC_APB2ENR, c->apb2 | TIM17EN);
  wr(TIM17 + 0x00, 0);                  /* CR1: stopped */
  wr(TIM17 + 0x28, 0);                  /* PSC: 96 MHz */
  wr(TIM17 + 0x2C, 0xFFFF);             /* ARR */
  wr(TIM17 + 0x14, 1);                  /* EGR: load them */
  wr(TIM17 + 0x00, 1);                  /* CR1: count */
  c->t = 0;
  c->c17 = rd(TIM17_CNT) & 0xFFFF, c->c14 = rd(TIM14_CNT) & 0xFFFF;
}

static void clk_off(clk_t *c) {
  wr(TIM17 + 0x00, 0);
  wr(RCC_APB2ENR, c->apb2);
}

/* busy-wait until cycle t: api->delay_us for all but the last 30 us, then the counter */
static void wait_to(const k6_api_t *api, clk_t *c, uint32_t t) {
  int32_t rem;
  while ((rem = (int32_t)(t - now(c))) > 96 * 60) {
    uint32_t us = (((uint32_t)rem * 683u) >> 16) - 30u;  /* rem / 96 (no divide on the M0) */
    api->delay_us(us > 200u ? 200u : us);
  }
  while ((int32_t)(t - now(c)) > 0) {}
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {     /* no divide on the M0: subtract powers of ten */
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

/* Morse by code, as cwkey.c: start at 1, then 0 for a dot and 1 for a dash */
static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";

static int code_of(char c) {
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c && c != '~') return i;
  return 0;
}

/* wait until ms() reaches t; 0, or -4 on a key */
static int until(const k6_api_t *api, uint32_t t) {
  if (api->key_down()) return -4;
  while ((int32_t)(api->ms() - t) < 0) api->delay_us(250);
  return 0;
}

/* still keyed? tx_pa(RT_PA) keeps the PA as it is (1 only re-enables the PA that tx_on enabled);
   -5 once the core keyed down */
static int keyed(const k6_api_t *api) {
  return api->tx_pa(RT_PA) < 0 ? -5 : 0;
}

/* AG7EW in MCW: the tone keyed with REG_70; the units sent, or a negative error */
static int mcw(const k6_api_t *api) {
  const char *s;
  int units = 0, r = 0;
  uint32_t t = api->ms();
  api->fd_write(0x70, 0);
  api->fd_write(0x71, WORD(ID_HZ));
  for (s = ID_TEXT; *s && !r; s++) {
    int c = code_of(*s), b;
    if (*s == ' ') { t += 4 * UNIT_MS; units += 4; continue; }
    for (b = 31; !(c >> b); b--) {}
    while (b-- && !r) {
      int n = (c >> b) & 1 ? 3 : 1, g = b ? 1 : 3;
      if ((r = keyed(api)) != 0) break;
      api->fd_write(0x70, REG70_ON);
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

static unsigned tone(uint32_t k) {
  return (RT_TONES[k >> 3] >> (7u - (k & 7u))) & 1u;
}

typedef struct {
  uint32_t w, late, wmin, wmax, dmax, amax;
} stat_t;

/* the burst: 0, or a negative error (the tone left on: the caller turns it off) */
static int burst(const k6_api_t *api, clk_t *c, stat_t *s) {
  uint32_t k, edge, t0, t1, len, tkey;
  unsigned cur = tone(0);
  int32_t a = 0;
  int r = 0, chk = 0;
  /* the write's length, from writes that change nothing yet (the tone is still off) */
  api->fd_write(0x71, cur ? W600 : W1200);
  len = 0;
  for (k = 0; k < 3; k++) {
    t0 = now(c);
    api->fd_write(0x71, cur ? W600 : W1200);
    t1 = now(c) - t0;
    if (t1 > len) len = t1;
  }
  s->wmin = s->wmax = len;
  if (len * (TWO ? 2u : 1u) > WR_MAX) return -8;
  /* bit 0's leading edge: the tone on */
  api->fd_write(0x70, REG70(cur ? RT_GAIN : RT_GAIN12));
  edge = tkey = now(c);
  for (k = 0; k < RT_NBITS; k++) {
    unsigned b = tone(k);
    if (b != cur) {
      /* a change at this edge: up (to 1200 Hz) late by d takes 600 Hz x d of phase off, down
         late by d puts it on; aim late by A going up, early by A going down */
      int32_t up = b ? -1 : 1, off = up * (a >> 8), d;
      off = off > OFF_MAX ? OFF_MAX : off < -OFF_MAX ? -OFF_MAX : off;
      uint32_t at = edge + (uint32_t)off - len;
      if ((int32_t)(at - now(c)) < 0)
        s->late++;
      wait_to(api, c, at);
      t0 = now(c);
      api->fd_write(0x71, b ? W600 : W1200);
      t1 = now(c);
      len = t1 - t0;
      if (TWO)                /* the gain right after: the bit's first write-length is at the
                                 other tone's level */
        api->fd_write(0x70, REG70(b ? RT_GAIN : RT_GAIN12));
      if (len < s->wmin) s->wmin = len;
      if (len > s->wmax) s->wmax = len;
      d = (int32_t)(t1 - edge);
      a -= up * d * 256;
      {
        uint32_t e = (uint32_t)(d - off < 0 ? off - d : d - off);
        if (e > s->dmax) s->dmax = e;
      }
      cur = b;
      s->w++;
      /* a key check or the core's keep-alive in the slack after a write, when the next change
         is two bits away or more (key_down takes about 60 us) */
      if ((uint32_t)(t1 - tkey) > KEY_EVERY && k + 1 < RT_NBITS && tone(k + 1) == b) {
        tkey = t1;
        if (chk ^= 1) {
          if (api->key_down()) return -4;
        } else {
          api->ms();
          if ((r = keyed(api)) != 0) return r;
        }
      }
    }
    a += cur ? DA600 : DA1200;
    {
      uint32_t m = (uint32_t)(a < 0 ? -a : a) >> 8;
      if (m > s->amax) s->amax = m;
    }
    edge += BIT;
  }
  wait_to(api, c, edge);     /* the last bit's end */
  return 0;
}

int k6_main(const k6_api_t *api) {
  char line[64], *p;
  clk_t c;
  stat_t s;
  uint32_t t;
  int r = 0, id = 0;
  s.w = s.late = s.wmin = s.wmax = s.dmax = s.amax = 0;   /* (an initialiser calls memclr) */
  if (api->level < 3) return -1;
  p = put(line, "rangetx n=");
  p = dec(p, RT_PACKETS);
  p = put(p, " bits=");
  p = dec(p, RT_NBITS);
  p = put(p, " g=");
  p = dec(p, RT_GAIN);
  p = put(p, "/");
  p = dec(p, RT_GAIN12);
  p = put(p, " pa=");
  p = dec(p, RT_PA);
  p = put(p, " rx=");
  p = dec(p, api->rx_hz());
  *p = 0;
  api->say(line);
  if (api->rx_hz() != RT_HZ) return -2;
  {                                           /* the upload's sender must have unkeyed */
    uint32_t clear = api->ms();
    while (api->ms() - clear < RT_CLEAR_MS) {
      if (KM_RSSI_DBM(api->fd_read(0x67)) >= RT_CLEAR_DBM) clear = api->ms();
      if (api->ms() > RT_WAIT_MS) return -7;
      if (api->key_down()) return -4;
      api->delay_us(10000);
    }
  }
  if (api->tx_on(TX_MS) != 0) return -3;
  if (!RT_PA) api->tx_pa(0);
  api->fd_write(0x70, 0);
  clk_on(&c);
  t = api->ms();
  r = until(api, t + RT_LEAD_MS);
  if (!r) r = burst(api, &c, &s);
  clk_off(&c);
  api->fd_write(0x70, 0);
  t = api->ms() - t - RT_LEAD_MS;
  p = put(line, "rangetx w=");
  p = dec(p, s.w);
  p = put(p, " late=");
  p = dec(p, s.late);
  p = put(p, " wr=");
  p = dec(p, s.wmin);
  p = put(p, "-");
  p = dec(p, s.wmax);
  p = put(p, " d=");
  p = dec(p, s.dmax);
  p = put(p, " a=");
  p = dec(p, s.amax);
  *p = 0;
  api->say(line);
  if (!r || r == -8) {
    if (!r) r = until(api, api->ms() + 400);  /* a gap, then the ID */
    else until(api, api->ms() + 400);
    id = mcw(api);
    if (id < 0) r = id;
  }
  api->fd_write(0x70, 0);
  api->fd_write(0x71, 0);
  api->tx_off();
  if (r) return r;
  p = put(line, "rangetx sent bits=");
  p = dec(p, RT_NBITS);
  p = put(p, " ms=");
  p = dec(p, t);
  p = put(p, " id=");
  p = dec(p, (uint32_t)id);
  *p = 0;
  api->say(line);
  return (int)RT_PACKETS;
}
