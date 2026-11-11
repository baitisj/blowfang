/* pocdump: what the FD6818's plain FSK receiver gives on POCSAG at 1200 and 2400 baud. A probe.

   pocsag_440's first bench run (2026-11-11 08:22Z, docs/k6-pocsag.md) read 512 baud perfectly,
   but at 1200 baud the chip found the sync codewords and then gave words more slowly than one
   every sixteen bits (27 to 92 empty FIFO reads a transmission), and the codewords were mostly
   wrong; at 2400 baud it never started. This module records the words for the host to lay
   against the bits that were sent (python3 -m modem.pocsag dump LOG), two ways:
     - the receiver at the line's own baud (does it drop bits, add them, or slip?);
     - the receiver at 4800 baud as a sampler of the line (four samples a bit of 1200 baud, two of
       2400), as the APRS work used it: is the line itself clean through the IC-7100's audio and
       the K6's filters? If so, the bits can be had from the samples, as aprsclk does for Bell 202.
   Settings of FE_S seconds after WAIT_MS, REG_2B + 0100 (the AF de-emphasis off: the 512 baud
   run's best) but where said:
      0  00e9  1200 baud  sync aaaa   the preamble at the line's baud
      1  00e9  4800       f0f0        the 1200 baud preamble at four samples a bit
      2  00e9  2400       aaaa
      3  00e9  4800       cccc        the 2400 baud preamble at two samples a bit
      4  00e5  1200       aaaa        RX bandwidth bits 2 (as good as 00e9 at 512)
      5  00e9  4800       f0f0        the AF path as found (de-emphasis on)
   A capture starts at the chip's first word and is read by time, LAG words behind, at REG_72's
   rate. Words that look like the preamble (every run of equal bits M samples long, give or take
   one) are skipped; from two words before the first that doesn't, DUMPW words are kept. A capture
   with fewer than 4 preamble words is noise: counted (z) and not said. From the first kept word
   on, a word like the one eight before is read again: the same again is the empty FIFO (kept as a
   gap, and the time base starts again from there), otherwise both are words. The same again is
   read again, since a sampled line has words alike in a row often enough: three alike (five at
   four samples a bit) is the empty FIFO, otherwise all are words (q counts them). Lines:
     pocdump start hz=440850000 n=6
     s 1 t=32000 cfg=00e9 b=c195 sy=f0f0 m=4 a=1   M: the chip's samples a bit of the line
     c 4 s=1 t=40012 u=1934 w=180 e=3 q=0 g=12 r=-23
                                               capture, setting, ms at its first word, ms to its
                                               last, words read, empty reads, two alike and one,
                                               preamble words, RSSI
     d 4 00 aaaa...                            the kept words, 12 a line (a gap: ----)
     v 1 n=7 z=2                               a setting's end: captures, noise starts
     pocdump end n=31
   CAPN captures said a setting. Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 2B, 3F and 02, put back
   as read. Receive only. Returns the captures, or -3 (old image), -8 (scratch).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#ifndef K6_MODULE_OPT
#define K6_MODULE_OPT z
#endif
#include "../k6_mod.h"

#ifndef DUMP_HZ
#define DUMP_HZ 440850000u
#endif
#ifndef DUMP_NAME
#define DUMP_NAME "pocdump"
#endif
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#ifndef FE_S
#define FE_S 40u
#endif
#ifndef SLOT_SCALE
#define SLOT_SCALE 1000u
#endif
#define LAG 5u
#ifndef DUMPW
#define DUMPW 150u       /* words kept: a batch and more at 1200 baud, 600 bits as samples */
#endif
#ifndef CAPN
#define CAPN 6u
#endif
#define GAP 0xFFFFFFFFu  /* a kept "word" that was the empty FIFO */
#define CTRL 0x00F0u
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u

typedef struct {
  uint16_t cfg, r72, wt, sync, af, m; /* REG_58, REG_72, a word in 1/16 ticks, sync word, REG_2B
                                         bits, samples a bit */
} var_t;

#ifdef DUMP_V
#define NV DUMP_NV
static const var_t V[NV] = {DUMP_V};
#else
#define NV 6
static const var_t V[NV] = {
    {0x00E9, 12389, 2133, 0xAAAA, 0x0100, 1},
    {0x00E9, 49557, 533, 0xF0F0, 0x0100, 4},
    {0x00E9, 24779, 1067, 0xAAAA, 0x0100, 1},
    {0x00E9, 49557, 533, 0xCCCC, 0x0100, 2},
    {0x00E5, 12389, 2133, 0xAAAA, 0x0100, 1},
    {0x00E9, 49557, 533, 0xF0F0, 0, 4},
};
#endif

static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F};
#define NSV sizeof(SAVED)
#define SAVED_2B 8

typedef struct {
  uint16_t sv[NSV];
  uint16_t ring[8];
  uint32_t keep[DUMPW];
  uint32_t el;
  uint16_t tl;
  char ln[64];
} st_t;

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static uint32_t udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
}

static char *dec(char *p, uint32_t v) {
  char t[10];
  int n = 0;
  do {
    uint32_t q = udiv(v, 10);
    t[n++] = (char)('0' + v - 10 * q);
    v = q;
  } while (v);
  while (n) *p++ = t[--n];
  return p;
}

static char *hexn(char *p, uint32_t v, int digits) {
  int i;
  for (i = 4 * (digits - 1); i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v); }

static void say(const k6_api_t *api, st_t *s, char *end) {
  *end = 0;
  api->say(s->ln);
}

static uint32_t swap(uint32_t w) { return (w & 0xFF) << 8 | w >> 8; }

/* every run of equal bits m long, give or take one (exactly one at m = 1); the end runs may be
   shorter */
static int prelike(uint32_t w, uint32_t m) {
  uint32_t i, run = 1, lo = m > 1 ? m - 1 : 1, hi = m > 1 ? m + 1 : 1, first = 1;
  for (i = 1; i < 16; i++) {
    if (((w >> i) ^ (w >> (i - 1))) & 1) {
      if (!first && run < lo) return 0;
      first = 0, run = 1;
    } else if (++run > hi) {
      return 0;
    }
  }
  return 1;
}

static uint32_t clk(const k6_api_t *api, st_t *s) {
  uint16_t t = (uint16_t)api->ticks();
  s->el += (uint16_t)(t - s->tl);
  s->tl = t;
  return s->el;
}

static uint32_t seek(const k6_api_t *api, const st_t *s, const var_t *v) {
  uint32_t i, w = 0;
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x2B, (uint16_t)(s->sv[SAVED_2B] | v->af));
  api->fd_write(0x5A, v->sync);
  api->fd_write(0x5B, v->sync);
  api->fd_write(0x58, v->cfg);
  api->fd_write(0x72, v->r72);
  api->fd_write(0x5C, 0x5625);
  api->fd_write(0x5D, 0xFFE0); /* 2048 bytes */
  api->fd_write(0x5E, 0x0206);
  for (i = 0; i < 2; i++) {
    api->fd_write(0x59, CTRL | CTRL_CLR);
    api->fd_write(0x59, CTRL | CTRL_EN);
    api->fd_write(0x02, 0);
    if (!i) api->delay_us(2000);
  }
  for (i = 0; i < 8; i++) w = api->fd_read(0x5F);
  return swap(w);
}

static void s_line(const k6_api_t *api, st_t *s, int cur, uint32_t now) {
  char *p = kv(s->ln, "s ", (uint32_t)cur);
  p = kv(p, " t=", now);
  p = hexn(put(p, " cfg="), V[cur].cfg, 4);
  p = hexn(put(p, " b="), V[cur].r72, 4);
  p = hexn(put(p, " sy="), V[cur].sync, 4);
  p = kv(p, " m=", V[cur].m);
  p = kv(p, " a=", V[cur].af >> 8);
  say(api, s, p);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, k, now, tkey = 0, tset, prev, t0 = 0, rn = 0, nk = 0, ne = 0, ng = 0, off = 0;
  uint32_t ncap = 0, total = 0, rpk = 0, tcap = 0, h1 = 0, h2 = 0, nz = 0, nq = 0;
  int key = 0, cur = 0, cap = 0, data = 0;
  char *p;
  const var_t *v = &V[0];
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < NSV; i++) s->sv[i] = api->fd_read(SAVED[i]);
  s->el = 0, s->tl = (uint16_t)api->ticks();
  api->tune(DUMP_HZ, 1);
  p = kv(s->ln, DUMP_NAME " start hz=", DUMP_HZ);
  p = kv(p, " n=", NV);
  say(api, s, p);
  while (api->ms() < WAIT_MS && !key) {
    api->delay_us(50000);
    key = api->level >= 4 ? api->key_poll() : api->key_down();
  }
  tset = api->ms();
  s_line(api, s, 0, tset);
  prev = seek(api, s, v);
  while (!key) {
    now = api->ms();
    if (now - tkey >= 50) {
      uint32_t r = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      if (r > rpk) rpk = r;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key) break;
    }
    if (!cap && now - tset >= FE_S * SLOT_SCALE) {
      p = kv(s->ln, "v ", (uint32_t)cur);
      p = kv(p, " n=", ncap);
      p = kv(p, " z=", nz);
      say(api, s, p);
      total += ncap, ncap = 0, nz = 0;
      if (++cur >= NV) break;
      v = &V[cur], tset = now;
      s_line(api, s, cur, now);
      prev = seek(api, s, v);
    }
    if (!cap) {
      uint32_t w = swap(api->fd_read(0x5F));
      if (w != prev) {
        cap = 1, rn = 1, nk = 0, ne = 0, ng = 0, nq = 0, off = 0, data = 0, h1 = h2 = w;
        t0 = clk(api, s) << 4, tcap = now, rpk = 0;
        s->ring[0] = (uint16_t)w;
        if (!v->m) data = 1, s->keep[nk++] = w; /* no preamble to skip: from the first word */
      }
    }
    while (cap && (int32_t)((clk(api, s) << 4) - t0 - (rn + LAG) * v->wt - off) >= 0) {
      uint32_t got[5], nw = 1, lim = v->m == 1 || v->m == 2 ? 3 : 5;
      got[0] = api->fd_read(0x5F);
      if (data && rn >= 8 && swap(got[0]) == s->ring[rn & 7]) {
        do got[nw] = api->fd_read(0x5F);
        while (got[nw++] == got[0] && nw < lim);
        if (got[nw - 1] == got[0]) {
          /* all alike: the empty FIFO. A gap, and the time base from here */
          ne++, nw = 0;
          off = (clk(api, s) << 4) - t0 - rn * v->wt;
          if (nk < DUMPW) s->keep[nk++] = GAP;
        } else if (nw > 2) {
          nq++; /* some alike and then another: the words as read */
        }
      }
      for (k = 0; k < nw; k++) {
        uint32_t x = swap(got[k]);
        s->ring[rn & 7] = (uint16_t)x, rn++;
        if (!data && prelike(x, v->m)) {
          ng++, h2 = h1, h1 = x; /* the preamble goes on */
        } else if (nk < DUMPW) {
          if (!data) { /* the two words before the first change, then the rest */
            data = 1;
            s->keep[nk++] = h2, s->keep[nk++] = h1;
          }
          s->keep[nk++] = x;
        }
      }
      if (nk < DUMPW && rn < 1000u) continue;
      cap = 0;
      if (v->m && ng < 4) {
        nz++;
      } else if (++ncap <= CAPN) {
        p = kv(s->ln, "c ", total + ncap);
        p = kv(p, " s=", (uint32_t)cur);
        p = kv(p, " t=", tcap);
        p = kv(p, " u=", now - tcap);
        p = kv(p, " w=", rn);
        p = kv(p, " e=", ne);
        p = kv(p, " q=", nq);
        p = kv(p, " g=", ng);
        p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(rpk));
        say(api, s, p);
        for (i = 0, k = 0; i < nk; i++, k = k == 11 ? 0 : k + 1) {
          if (!k) {
            p = kv(s->ln, "d ", total + ncap);
            *p++ = ' ', p = hexn(p, i, 2), *p++ = ' ';
          }
          p = s->keep[i] == GAP ? put(p, "----") : hexn(p, s->keep[i], 4);
          if (k == 11 || i + 1 == nk) say(api, s, p);
        }
      }
      prev = seek(api, s, v);
    }
    api->delay_us(300);
  }
  for (i = 0; i < NSV; i++) api->fd_write(SAVED[i], s->sv[i]);
  api->fd_write(0x02, 0);
  total += ncap;
  p = kv(s->ln, DUMP_NAME " end n=", total);
  say(api, s, p);
  return (int)total;
}
