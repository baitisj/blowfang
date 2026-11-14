/* samerx: NOAA SAME headers through the FD6818's plain FSK receiver used as a sampler of the audio.

   SAME (47 CFR 11.31): AFSK on the FM audio, mark 2083.3 Hz (a 1), space 1562.5 Hz (a 0), 520.83
   baud, bytes low bit first; sixteen bytes of AB, then the text ("ZCZC-WXR-RWT-..." or "NNNN"),
   sent three times. samecap showed the receiver at SAME's own baud never starts on the tones. Here
   it runs as a sampler (as aprsdfsk reads Bell 202): it slices the discriminator's sign SR times a
   second and hands over 16 samples a word, and the module tells the tones apart over each bit's
   samples itself:
     - a bit is L = SR / 520.83 samples; over them the samples are laid against both tones (tables
       of cos and sin, 64 at full scale), and the louder tone is the bit;
     - the bit clock: over the first 24 bits every phase is tried and the middle of the best kept,
       then every eight bits it moves toward the better of a sample early or late, half a sample
       at most;
     - the preamble byte AB (low bit first) sets the bytes; then text up to the first byte that is
       not printable. A capture without AB in its first 80 bits, or with fewer than two of them, is
       noise or speech: counted (z) and not said.
   The sync word that starts the chip on the preamble depends on the rate: words the host's model
   (modem/same.py) finds in every preamble at that rate. Settings (SAME_BENCH: three of FE_S
   seconds after WAIT_MS; otherwise SAME_RATE's, for FE_S seconds):
      0  6347 a second (REG_72 at its top)  sync 6666
      1  4800                               952a
      2  4166.7 (8 a bit: the mark at two samples a cycle)  aaaa
   Lines:
     samerx start hz=162550000 n=1
     s 0 t=1 b=ffff sy=6666
     h 3 s=0 t=12345 p=14 n=46 r=-71          a header: setting, ms, AB bytes, characters, RSSI
     + ZCZC-WXR-RWT-053033+0015-3151800-KSEW/NWS-   its text, 56 a line
     v 0 n=3 z=17                             a setting's end: headers, captures without one
     samerx end n=3 z=17
   Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72 and 3F, put back as read. Receive only (a public
   weather broadcast). Returns the headers, or -3 (old image), -8 (scratch).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#ifndef K6_MODULE_OPT
#define K6_MODULE_OPT z
#endif
#include "../k6_mod.h"
#include "k6ui.h"
#ifndef K6_NAME
#define K6_NAME "SAME RX"   /* its name in the MENU list (a file that includes this one may set its own) */
#endif
K6_MODULE_NAME(K6_NAME);

#ifndef SAME_HZ
#define SAME_HZ 162550000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 0u
#endif
#ifndef FE_S
#define FE_S 7200u
#endif
#ifndef SLOT_SCALE
#define SLOT_SCALE 1000u
#endif
#ifdef SAME_BENCH
#define NV 3
#define RATE0 0
#else
#define NV 1
#ifndef SAME_RATE
#define SAME_RATE 0     /* the setting below that the bench reads best */
#endif
#define RATE0 SAME_RATE
#endif
#define LAG 4u
#define ACQ 24u         /* bits with every phase tried */
#define HUNT 80u        /* bits to find AB in */
#define TXTN 240u
#define CTRL 0x00F0u
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u

typedef struct {
  uint16_t r72, wt, sync, step; /* REG_72, a word in 1/16 ticks, sync word, samples a bit x 256 */
  uint8_t l;                    /* samples a bit, rounded */
  int8_t t[4][12];              /* mark cos, sin, space cos, sin; x 64 */
} rate_t;

static const rate_t R[3] = {
    {65535, 403, 0x6666, 3120, 12,
     {{64, -30, -35, 64, -25, -40, 63, -19, -45, 61, -13, -49},
      {0, -56, 53, 6, -59, 50, 12, -61, 45, 18, -63, 41},
      {64, 2, -64, -5, 64, 8, -63, -11, 63, 14, -62, -17},
      {0, -64, -3, 64, 6, -64, -9, 63, 12, -62, -15, 62}}},
    {49557, 533, 0x952A, 2359, 9,
     {{64, -59, 43, -21, -6, 31, -51, 62, -63},
      {0, -26, 47, -61, 64, -56, 39, -15, -11},
      {64, -29, -37, 63, -21, -45, 61, -11, -51},
      {0, -57, 52, 9, -61, 46, 19, -63, 39}}},
    {43018, 614, 0xAAAA, 2048, 8,
     {{64, -64, 64, -64, 64, -64, 64, -64},
      {0},
      {64, -45, 0, 45, -64, 45, 0, -45},
      {0, -45, 64, -45, 0, 45, -64, 45}}},
};

static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x3F};
#define NSV sizeof(SAVED)

enum { ACQUIRE, HUNTING, PRE, TEXT, DONE };

typedef struct {
  uint16_t sv[NSV];
  uint16_t ring[8];
  uint8_t sm[256];     /* the samples, by their number */
  uint32_t ns;         /* samples in */
  uint32_t pos;        /* the next bit's first sample x 256 */
  uint32_t acc[12];    /* acquiring: each phase's |mark - space| */
  int32_t e, c, lt;    /* tracking: early, on time, late over eight bits */
  uint32_t nb, np, nt; /* bits since the start, AB bytes, characters */
  uint8_t st, sr, k;
  uint32_t el;
  uint16_t tl;
  char txt[TXTN + 1];
  char ln[64];
  ui_t u;                       /* the screen (k6ui.h) */
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

static uint32_t clk(const k6_api_t *api, st_t *s) {
  uint16_t t = (uint16_t)api->ticks();
  s->el += (uint16_t)(t - s->tl);
  s->tl = t;
  return s->el;
}

static int32_t iabs(int32_t v) { return v < 0 ? -v : v; }

static int32_t mag(int32_t i, int32_t q) {
  i = iabs(i), q = iabs(q);
  return i > q ? i + (q >> 1) : q + (i >> 1);
}

/* mark against space over the bit's samples from sample i */
static int32_t metric(const st_t *s, const rate_t *r, uint32_t i) {
  int32_t a = 0, b = 0, c = 0, d = 0;
  uint32_t k;
  for (k = 0; k < r->l; k++) {
    int32_t x = s->sm[(i + k) & 255] ? 1 : -1;
    a += x * r->t[0][k], b += x * r->t[1][k], c += x * r->t[2][k], d += x * r->t[3][k];
  }
  return mag(a, b) - mag(c, d);
}

/* a byte (low bit first) in sr after eight bits: what comes of it */
static void byte_in(st_t *s) {
  uint8_t c = s->sr;
  if (s->st == PRE && c == 0xAB) {
    s->np++;
  } else if (c >= 32 && c < 127 && s->nt < TXTN) {
    s->st = TEXT;
    s->txt[s->nt++] = (char)c;
  } else {
    s->st = DONE;
  }
}

static void bit_in(st_t *s, uint32_t bit) {
  s->sr = (uint8_t)(s->sr >> 1 | bit << 7);
  if (s->st == HUNTING) {
    if (s->sr == 0xAB) s->st = PRE, s->np = 1, s->k = 0;
    else if (s->nb > ACQ + HUNT) s->st = DONE;
    return;
  }
  if (++s->k == 8) s->k = 0, byte_in(s);
}

/* the bits the samples in make */
static void bits(st_t *s, const rate_t *r) {
  uint32_t o;
  while (s->st != DONE && (s->pos >> 8) + (s->st == ACQUIRE ? 2u * r->l : r->l) + 2 < s->ns) {
    uint32_t i = (s->pos + 128) >> 8;
    if (s->st == ACQUIRE) {
      for (o = 0; o < r->l; o++) s->acc[o] += (uint32_t)iabs(metric(s, r, i + o));
      if (++s->nb == ACQ) {
        /* the middle of the best: each phase with its neighbours (the mark alone at two samples a
           cycle is as loud at every phase) */
        uint32_t best = 0, top = 0;
        for (o = 0; o < r->l; o++) {
          uint32_t v = s->acc[o] + s->acc[o ? o - 1 : r->l - 1] + s->acc[o + 1 < r->l ? o + 1 : 0];
          if (v > top) top = v, best = o;
        }
        s->pos += best << 8;
        s->st = HUNTING;
      }
    } else {
      int32_t m = metric(s, r, i);
      s->c += iabs(m), s->e += iabs(metric(s, r, i - 1)), s->lt += iabs(metric(s, r, i + 1));
      s->nb++;
      bit_in(s, m > 0);
      if (!(s->nb & 7)) {
        /* toward the better side, half a sample at most */
        int32_t d = s->lt - s->e;
        uint32_t q = udiv((uint32_t)iabs(d) << 7, (uint32_t)s->c + 1);
        if (q > 128) q = 128;
        s->pos += d < 0 ? (uint32_t)-(int32_t)q : q;
        s->e = s->c = s->lt = 0;
      }
    }
    s->pos += r->step;
  }
}

static uint32_t seek(const k6_api_t *api, const rate_t *r) {
  uint32_t i, w = 0;
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x5A, r->sync);
  api->fd_write(0x5B, r->sync);
  api->fd_write(0x58, 0x00E9);
  api->fd_write(0x72, r->r72);
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
  p = hexn(put(p, " b="), R[RATE0 + cur].r72, 4);
  p = hexn(put(p, " sy="), R[RATE0 + cur].sync, 4);
  say(api, s, p);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, k, now, tkey = 0, tset, prev, t0 = 0, rn = 0, off = 0, rpk = 0, tcap = 0;
  uint32_t nh = 0, nz = 0, total = 0, tz = 0;
  int key = 0, cur = 0, cap = 0;
  char *p;
  const rate_t *r = &R[RATE0];
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < NSV; i++) s->sv[i] = api->fd_read(SAVED[i]);
  s->el = 0, s->tl = (uint16_t)api->ticks();
  api->tune(SAME_HZ, 1);
  p = kv(s->ln, "samerx start hz=", SAME_HZ);
  p = kv(p, " n=", NV);
  say(api, s, p);
  ui_mhz(ui_put(s->ln, "SAME "), SAME_HZ);
  ui_begin(&s->u, api, s->ln, "WAITING  KEY: STOP");
  s->u.lt = 3; /* the log: the newest header, two lines */
  while (api->ms() < WAIT_MS && !key) {
    api->delay_us(50000);
    key = api->level >= 4 ? api->key_poll() : api->key_down();
  }
  tset = api->ms();
  s_line(api, s, 0, tset);
  prev = seek(api, r);
  while (!key) {
    now = api->ms();
    if (now - tkey >= 50) {
      uint32_t v = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      if (v > rpk) rpk = v;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key) break;
    }
    if (s->u.on && !cap && now - s->u.t_fl >= 1000) {
      /* the screen's status between bursts (a flush is 20-40 ms of not reading) */
      p = ui_mmss(s->ln, ui_udiv(now, 1000));
      p = ui_sdec(ui_put(p, "  "), KM_RSSI_DBM(api->fd_read(0x67)));
      p = ui_dec(ui_put(p, "DBM H"), total + nh, 1);
      ui_text(&s->u, 5, s->ln);
      ui_now(&s->u);
    }
    if (!cap && now - tset >= FE_S * SLOT_SCALE) {
      p = kv(s->ln, "v ", (uint32_t)cur);
      p = kv(p, " n=", nh);
      p = kv(p, " z=", nz);
      say(api, s, p);
      total += nh, tz += nz, nh = nz = 0;
      if (++cur >= NV) break;
      r = &R[RATE0 + cur], tset = now;
      s_line(api, s, cur, now);
      prev = seek(api, r);
    }
    if (!cap) {
      uint32_t w = swap(api->fd_read(0x5F));
      if (w != prev) {
        cap = 1, rn = 1, off = 0, t0 = clk(api, s) << 4, tcap = now, rpk = 0;
        s->ring[0] = (uint16_t)w;
        for (k = 0; k < 16; k++) s->sm[k] = (uint8_t)(w >> (15 - k) & 1);
        s->ns = 16, s->pos = 0, s->nb = s->np = s->nt = 0, s->st = ACQUIRE, s->sr = 0, s->k = 0;
        s->e = s->c = s->lt = 0;
        for (k = 0; k < 12; k++) s->acc[k] = 0;
      }
    }
    while (cap && (int32_t)((clk(api, s) << 4) - t0 - (rn + LAG) * r->wt - off) >= 0) {
      uint32_t got[2], nw = 1;
      got[0] = api->fd_read(0x5F);
      if (r->l > 8 && rn >= 8 && swap(got[0]) == s->ring[rn & 7]) {
        /* like the word eight before: read again, and the same again is the empty FIFO (at 4800
           and 6347 a second two words alike in a row like the ones eight before never come in
           the host's model; at 4166.7 the mark's 1010 does it all the time, so there the words
           are read by time alone) */
        got[1] = api->fd_read(0x5F), nw = 2;
        if (got[1] == got[0]) {
          nw = 0;
          off = (clk(api, s) << 4) - t0 - rn * r->wt;
        }
      }
      for (i = 0; i < nw; i++) {
        uint32_t x = swap(got[i]);
        s->ring[rn & 7] = (uint16_t)x, rn++;
        for (k = 0; k < 16; k++) s->sm[(s->ns + k) & 255] = (uint8_t)(x >> (15 - k) & 1);
        s->ns += 16;
      }
      bits(s, r);
      if (s->st != DONE && rn < 1000u) continue;
      cap = 0;
      if (s->np < 2 || s->nt == 0) {
        nz++;

      } else {
        /* a header ends at its last '-' (what follows is the silence after it) */
        if (s->txt[0] == 'Z') {
          for (k = s->nt; k > 1 && s->txt[k - 1] != '-'; k--) {
          }
          if (k > 1) s->nt = k;
        }
        nh++;
        p = kv(s->ln, "h ", total + nh);
        p = kv(p, " s=", (uint32_t)cur);
        p = kv(p, " t=", tcap);
        p = kv(p, " p=", s->np);
        p = kv(p, " n=", s->nt);
        p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(rpk));
        say(api, s, p);
        for (i = 0; i < s->nt; i += 56) {
          p = put(s->ln, "+ ");
          for (k = i; k < s->nt && k < i + 56; k++) *p++ = s->txt[k];
          say(api, s, p);
        }
        if (s->u.on) {
          /* the screen: the header in two lines, its fields apart ("WXR RWT 053033+0015 ..."),
             ZCZC left out */
          k = (s->nt > 5 && s->txt[0] == 'Z') ? 5 : 0;
          for (i = 0; i < 2 && k < s->nt; i++) {
            uint32_t j = 0;
            while (j < UI_COLS && k < s->nt) {
              char c = s->txt[k++];
              s->ln[j++] = c == '-' ? ' ' : c;
            }
            s->ln[j] = 0;
            ui_log(&s->u, s->ln);
          }
          p = ui_dec(ui_put(s->ln, "HEADERS HEARD: "), total + nh, 1);
          ui_text(&s->u, 1, s->ln);
        }
      }
      prev = seek(api, r);
    }
    api->delay_us(300);
  }
  for (i = 0; i < NSV; i++) api->fd_write(SAVED[i], s->sv[i]);
  api->fd_write(0x02, 0);
  total += nh, tz += nz;
  p = kv(s->ln, "samerx end n=", total);
  p = kv(p, " z=", tz);
  say(api, s, p);
  ui_end(&s->u, key ? "STOPPED" : "DONE", key);
  return (int)total;
}
