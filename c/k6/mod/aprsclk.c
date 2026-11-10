/* aprsclk: set a clock from APRS traffic heard on 144.390 MHz (docs/k6-aprs-clock.md).

   Receive only. The K6 tunes to APRS_HZ (wide filter, the chip's AFC left on), taps the
   FD6818's low IF on PB1 (GP0 code 13) by DMA as the FT8 modules do (gp0rx.h, PR #69), and
   demodulates Bell 202 AFSK 1200 on the MCU:

     counts (2 * ones - 64 per 64 DMA samples, ~87 kHz)
       -> mixed down by the nominal IF (64-entry sine), a 2nd-order CIC decimating by 6 (~14.5 kHz)
       -> phase by CORDIC; the phase step is the FM discriminator (no division)
       -> DC removed, one-bit sliding correlators at 1200 and 2200 Hz: each tone's deviation
          (tracked where that tone wins) and the carrier offset (the DC)
       -> a DPLL bit clock on the discriminator's mark / space (Dire Wolf's: nudged towards each
          transition)
       -> each bit decided by a noncoherent matched filter on the IF (sim/k6if/aprs_mf.py: about
          4 dB more sensitive than deciding on the discriminator): the IF turned back by the
          offset, summed over the bit against exp(j beta sin(2 pi f t + theta)) for each tone,
          beta = its tracked deviation / f, theta at 16 audio phases; mark against space on the
          largest |sum| of each (-DAPRS_DISC builds the old discriminator decision), then NRZI
       -> HDLC (flags, bit stuffing), CRC-16/X.25 (good residue 0xF0B8)
       -> the APRS information field's timestamps:
            '/' '@' positions, ';' objects, '>' status:  DDHHMMz (60 s resolution), HHMMSSh (1 s)
            '$GPRMC' / '$GNRMC' raw NMEA (status A):     hhmmss[.ss] and ddmmyy
            '_' positionless weather:                    MMDDHHMM (60 s; many send local time, so
                                                         weather stamps alone never set the clock)
          ("DDHHMM/" local time, Mic-E, '}' third party and the rest carry no usable time)

   Each timestamp says the true UTC at the frame's first bit was in [stamp, stamp + resolution +
   delay], delay 3 s heard direct, 8 s digipeated (an H bit in the path). In offsets
   (UTC ms of day less the module's ms) those intervals go into a table, one per station (a
   digipeated copy of the same stamp is not a second vote), and Marzullo's algorithm (NTP's)
   picks the offset the most stations agree on, the narrowest such interval among ties. With two
   or more stations agreeing, the clock is set: its estimate is the interval's low edge plus half
   its width, at most 0.5 s (a stamp can't be later than the send, and trackers key within a
   second of the fix); with minute stamps only (a region wider than 10 s) its middle, so within
   about 30 s. A 1 s stamp gives the second; minute stamps alone give the minute.

   Lines (serial / telemetry):
     aprs start hz=144390000 ks=5603 n=12 g=1023           (g: REG_13, the gain frozen there)
     m 3 rssi=-104 pk=-85 pre=1 ok=1                       (a minute: floor, peak, preambles, good)
     f VE7ABC-9 n=58 h=1 ts=175201h                        (a good frame: source, bytes, hops, stamp)
     clk 17:52:04.250 w=1000 v=2 d=08                      (the clock: UTC, interval ms, votes, day)
     aprs end ok=12 bad=3 ts=5 v=2 utc=17:59:59.120 lost=0
   Each clock line with two or more votes also goes to the radio's clock (level 8, clock_set):
   UTC now, the date it has, and a bound of the interval's far end from the estimate.
   Screen (level 6) once a second when no frame is coming (UTC and date, votes, packets heard / decoded /
   stamped, the station heard last), LED blink a good frame (level 7). A key ends it (else RUN_MS). Writes REG_34[3:0]
   and REG_33 bit 8 (GP0), REG_7E and REG_13 (the gain), the MCU's TIM17, DMA channel 1 and their clock enables, all put back.
   Returns the votes behind the clock (0: none), or -2 (a key before any frame), -3 (old image),
   -4 (PB1 not an input), -5 (REG_33 refused), -7 (TIM17 or DMA channel 1 in use), -8 (scratch).

   -DAPRS_FSK (aprsfsk.c) builds the same HDLC, parser, vote and screen behind the FD6818's own
   FSK modem instead of the IF tap: see "the chip's own FSK modem as the front end" below.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#ifndef K6_MODULE_OPT
#define K6_MODULE_OPT s
#endif
#include "../k6_mod.h"

#ifndef APRS_HZ
#define APRS_HZ 144390000u
#endif
#ifndef RUN_MS
#define RUN_MS 900000u /* 15 min */
#endif
#ifndef GAIN13
#define GAIN13 0x03FFu /* REG_13 with the AGC frozen (the maximum); 0 leaves the AGC running */
#endif
#define DMAX_DIRECT 3000
#define DMAX_DIGI 8000
#define NC 12   /* stations kept */
#define MAXF 332 /* AX.25 frame bytes, with the FCS */
#define DEC 6   /* counts an audio sample */
#define NW 16   /* correlator ring (the window is n of it) */
#define GN (64 + 28) /* a template's phases kept: index + 4 * 7 never wraps */
#ifndef PLL_IN
#ifdef APRS_DISC
#define PLL_IN 2
#else
#define PLL_IN 4 /* inside a frame each transition pulls the bit clock 1 / 2^PLL_IN of the way */
#endif
#endif
#define FIF_CHZ 1692708u /* 26 MHz / 1536 in 0.01 Hz */
#define DAY 86400000

#define GPIOB 0x48000400u
#define RCC_AHBENR 0x40021014u
#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT 0x40002024u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define DMA 0x40020000u
#define DMA_CCR1 (DMA + 0x08)
#define DMA_CNDTR1 (DMA + 0x0C)
#define DMA_CPAR1 (DMA + 0x10)
#define DMA_CMAR1 (DMA + 0x14)
#define TIM17EN (1u << 18)
#define RSZ 1024u


/* a station's interval: offsets (UTC ms of day less module ms) relative to the table's ref */
typedef struct {
  uint8_t call[7];  /* address bytes as sent (SSID byte masked to its SSID) */
  uint8_t kind;     /* 'z', 'h', 'r' (RMC) or 'w' (weather MMDDHHMM), 0 empty */
  int32_t lo, hi;
  uint32_t age;     /* insertion count, for replacing the oldest */
  uint32_t key;     /* the stamp (ms of day, day << 27), to spot a digipeated copy */
  uint8_t day, mon, yr, pad;
} cand_t;

typedef struct {
  uint8_t ring[RSZ];           /* first: word aligned at the scratch's start */
  uint8_t fr[MAXF];
  int32_t cr[4][NW];           /* the correlators' products: mark cos, mark sin, space cos, space sin */
  cand_t c[NC];
  char ln[64];
  char last[16];               /* the newest good frame's source and hops, for the screen */
  /* clock and DMA */
  uint32_t t, c17, c14, laps, lastw, produced, consumed, t0, p0, cps8, lost;
  /* mixer and CIC */
  uint32_t ph, dph;
  int32_t i1, i2, q1, q2, ci, cq, di, dq, k;
  /* discriminator, correlators, bit clock */
  int32_t ang, dc, sum[4];
  uint32_t ph1, ph2, st1, st2, pll, stb;
  int32_t n, w, lastv, lastbit;
  /* the matched filter: each tone's template exp(j beta sin) at 64 phases (cos, sin) */
  int8_t g[2][GN][2];          /* 64 a turn, the first GN - 64 again after them */
  int32_t acc[2][8][4];        /* this bit's sums for 8 audio phases (x gc, y gs, y gc, x gs) */
  int32_t bq[2], btab[2];      /* beta in 1/16 of a template step (64 a turn): tracked (Q6), tabled */
  uint32_t rot;                /* the offset's phase, 2^16 a turn */
  int32_t mlast;               /* the last bit's tone */
  /* HDLC */
  uint32_t sr, bb, nbits, flag_ms, run, pre, min_ms;
  int32_t len, in;
  /* results */
  uint32_t ok, bad, nts, seq, now_ms, scr_ms;
  uint16_t rlo, rhi; /* REG_67 once a second: the minute's least and most */
  int32_t ref, haveref, votes, est, wid, hops;
  uint8_t day, mon, yr, stn;
#ifdef APRS_FSK
  uint32_t fake;      /* sync words with under 6 words and no frame */
  uint16_t sv[10];    /* the modem's registers as found */
#ifdef FSK_PLAIN
  uint16_t fifo[8];   /* the last eight words taken: what a read of the empty FIFO gives again */
  int32_t dlast, dn, dtone, ddur, dmd; /* the slicer: the last sample, samples since a transition,
                                          the run's tone and length, a mark run held back */
  uint32_t idle;      /* line bits outside a frame */
#else
  uint16_t cap[128];  /* a packet's words as read, for its d lines */
#endif
#endif
} st_t;

#ifndef APRS_FSK
static const int8_t SIN[64] = {0,   12,  25,  37,  49,  60,  71,  81,  90,  98,  106, 112, 117,
                               122, 125, 126, 127, 126, 125, 122, 117, 112, 106, 98,  90,  81,
                               71,  60,  49,  37,  25,  12,  0,   -12, -25, -37, -49, -60, -71,
                               -81, -90, -98, -106, -112, -117, -122, -125, -126, -127, -126, -125,
                               -122, -117, -112, -106, -98, -90, -81, -71, -60, -49, -37, -25, -12};
/* atan(2^-i) in 2^16 a turn */
static const uint16_t ATN[11] = {8192, 4836, 2555, 1297, 651, 326, 163, 81, 41, 20, 10};
#endif

static uint32_t udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
}

/* a * b / c in 64 bits by hand (an M0 module has no run-time library), c < 2^31 */
#ifndef APRS_FSK
static uint32_t muldiv(uint32_t a, uint32_t b, uint32_t c) {
  uint32_t al = a & 0xFFFF, ah = a >> 16, bl = b & 0xFFFF, bh = b >> 16;
  uint32_t lo = al * bl, m1 = al * bh, m2 = ah * bl, hi = ah * bh, t, r = 0, q = 0;
  int i;
  t = lo + (m1 << 16);
  hi += (m1 >> 16) + (t < lo);
  lo = t, t = lo + (m2 << 16);
  hi += (m2 >> 16) + (t < lo);
  lo = t;
  for (i = 0; i < 64; i++) {
    r = (r << 1) | (hi >> 31);
    hi = (hi << 1) | (lo >> 31);
    lo <<= 1;
    q <<= 1;
    if (r >= c) r -= c, q |= 1;
  }
  return q;
}

#endif

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

/* v in decimal, at least w digits */
static char *dec(char *p, uint32_t v, int w) {
  char t[10];
  int n = 0;
  do {
    uint32_t q = udiv(v, 10);
    t[n++] = (char)('0' + v - 10 * q);
    v = q;
  } while (v || n < w);
  while (n) *p++ = t[--n];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v, 1); }

/* ms of day -> hh:mm:ss (and .mmm) */
static char *hms(char *p, uint32_t ms, int frac) {
  uint32_t s = udiv(ms, 1000), m = udiv(s, 60), h = udiv(m, 60);
  p = dec(p, h, 2);
  *p++ = ':';
  p = dec(p, m - 60 * h, 2);
  *p++ = ':';
  p = dec(p, s - 60 * m, 2);
  if (frac) *p++ = '.', p = dec(p, ms - 1000 * s, 3);
  return p;
}

/* a callsign from its 7 address bytes: "VE7ABC-9" */
static char *call(char *p, const uint8_t *a) {
  int i, ssid = (a[6] >> 1) & 15;
  for (i = 0; i < 6 && (a[i] >> 1) != ' '; i++) *p++ = (char)(a[i] >> 1);
  if (ssid) *p++ = '-', p = dec(p, (uint32_t)ssid, 1);
  return p;
}

static void say(const k6_api_t *api, st_t *s, char *p) {
  *p = 0;
  api->say(s->ln);
}

/* ---- the IF front end (gp0rx.h's, without the FT8 mixer) ---- */
#ifndef APRS_FSK
#ifndef APRS_HOST
static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static uint32_t now(st_t *s) {
  uint32_t c17 = rd(TIM17_CNT) & 0xFFFF, c14 = rd(TIM14_CNT) & 0xFFFF;
  uint32_t coarse = ((c14 - s->c14) & 0xFFFF) * 9600u, fine = (c17 - s->c17) & 0xFFFF;
  int32_t d = (int32_t)(int16_t)(uint16_t)(fine - coarse);
  s->c17 = c17, s->c14 = c14;
  return s->t += coarse + (uint32_t)d;
}

static uint32_t poll(st_t *s) {
  uint32_t w = RSZ - (rd(DMA_CNDTR1) & 0xFFFF);
  if (w < s->lastw) s->laps++;
  s->lastw = w;
  return s->produced = s->laps * RSZ + w;
}

static void resync(st_t *s) {
  uint32_t w = RSZ - (rd(DMA_CNDTR1) & 0xFFFF);
  s->laps = 0, s->lastw = w, s->produced = w;
  s->consumed = s->p0 = w & ~63u;
  s->t0 = now(s);
}

static int ones64(const uint8_t *q) {
  const uint32_t *p = (const uint32_t *)q, m = 0x02020202u;
  uint32_t a = 0, i;
  for (i = 0; i < 16; i += 4) a += (p[i] & m) + (p[i + 1] & m) + (p[i + 2] & m) + (p[i + 3] & m);
  a >>= 1;
  a = (a & 0x00FF00FFu) + ((a >> 8) & 0x00FF00FFu);
  return (int)((a & 0xFFFF) + (a >> 16));
}
#endif

/* the steps from the DMA's rate (cps8: 96 MHz cycles a transfer, Q8). Counts a second
   R = 3.84e8 / cps8, audio fa = R / DEC */
static void steps(st_t *s) {
  uint32_t c = s->cps8;
  s->dph = muldiv(FIF_CHZ << 4, c << 16, 9375000u);            /* 2^32 FIF / R */
  s->st1 = muldiv(1200u * c * DEC, 1u << 26, 6000000u);        /* 2^32 f / fa */
  s->st2 = muldiv(2200u * c * DEC, 1u << 26, 6000000u);
  s->stb = s->st1;                                             /* 1200 baud */
}
#endif /* APRS_FSK */

/* ---- the clock: Marzullo over the stations' intervals ---- */
static void vote(st_t *s) {
  int i, j, best = 0, bw = 0, blo = 0;
  for (i = 0; i < NC; i++) {
    int32_t x = s->c[i].lo, hi = 0x7FFFFFFF;
    int n = 0, sn = 0;
    if (!s->c[i].kind) continue;
    for (j = 0; j < NC; j++)
      if (s->c[j].kind && s->c[j].lo <= x && x <= s->c[j].hi) {
        n++, sn += s->c[j].kind != 'w';
        if (s->c[j].hi < hi) hi = s->c[j].hi;
      }
    if (!sn && n > 1) n = 1; /* weather stations in one zone sending local time agree with each other */
    if (n > best || (n == best && hi - x < bw)) best = n, bw = hi - x, blo = x;
  }
  s->votes = best, s->wid = bw;
  /* a second-stamp interval: half a second in (the usual stamp-to-key lead); minute stamps only
     (wider than 10 s): its middle, so never more than about 30 s off */
  s->est = blo + (bw > 10000 ? bw / 2 : bw / 2 < 500 ? bw / 2 : 500);
  /* the date: the newest agreeing stamp that has one */
  for (i = 0, j = -1; i < NC; i++)
    if (s->c[i].kind && s->c[i].day && s->c[i].lo <= s->est && s->est <= s->c[i].hi &&
        (j < 0 || s->c[i].age > s->c[j].age))
      j = i;
  if (j >= 0) s->day = s->c[j].day, s->mon = s->c[j].mon, s->yr = s->c[j].yr;
}

static int32_t wrap(int32_t v) {
  if (v > DAY / 2) v -= DAY;
  if (v <= -DAY / 2) v += DAY;
  return v;
}

/* UTC ms of day at module ms t, once set */
static uint32_t utc(st_t *s, uint32_t t) {
  int32_t v = (int32_t)t + s->ref + s->est;
  if (v < 0) v += DAY;
  if (v < 0) v += DAY;
  if (v >= DAY) v -= DAY;
  if (v >= DAY) v -= DAY;
  return (uint32_t)v;
}

static int same(const uint8_t *a, const uint8_t *b, int n) {
  while (n--)
    if (*a++ != *b++) return 0;
  return 1;
}

static int d2(const uint8_t *p) {
  if (p[0] < '0' || p[0] > '9' || p[1] < '0' || p[1] > '9') return -99;
  return (p[0] - '0') * 10 + p[1] - '0';
}

/* six digits as three two-digit fields under hi0 (24 for an hour, 32 for a day), 60, 60: x * 3600
   + y * 60 + z (a day-hour-minute: day * 3600 + hour * 60 + minute), or -1 */
static int32_t tri(const uint8_t *p, int hi0) {
  int x = d2(p), y = d2(p + 2), z = d2(p + 4);
  if (x < 0 || x >= hi0 || y < 0 || y >= 60 || z < 0 || z >= 60) return -1;
  return (x * 60 + y) * 60 + z;
}

/* a stamp at src (7 address bytes): ms of day stamped, resolution, frame start (module ms) */
static void add(const k6_api_t *api, st_t *s, const uint8_t *src, int kind, uint32_t tod, int res,
                uint32_t t0, int day, int mon, int yr) {
  uint32_t key = tod | (uint32_t)day << 27;
  int i, k = -1;
  int32_t lo;
  cand_t *c;
  char *p;
  if (!s->haveref) s->ref = (int32_t)tod - (int32_t)t0, s->haveref = 1;
  lo = wrap((int32_t)tod - (int32_t)t0 - s->ref);
  for (i = 0; i < NC; i++) {
    c = &s->c[i];
    if (c->kind && same(c->call, src, 6) && ((c->call[6] ^ src[6]) & 0x1E) == 0) {
      if (c->kind == kind && c->key == key) return; /* a digipeated copy */
      k = i;
      break;
    }
    if (k < 0 || !c->kind || (s->c[k].kind && c->age < s->c[k].age)) k = i;
  }
  c = &s->c[k];
  for (i = 0; i < 6; i++) c->call[i] = src[i];
  c->call[6] = src[6] & 0x1E, c->key = key;
  c->kind = (uint8_t)kind, c->lo = lo, c->hi = lo + res + (s->hops ? DMAX_DIGI : DMAX_DIRECT);
  c->age = ++s->seq, c->day = (uint8_t)day, c->mon = (uint8_t)mon, c->yr = (uint8_t)yr;
  s->nts++;
  vote(s);
  p = put(s->ln, "clk ");
  if (s->votes >= 2) p = hms(p, utc(s, s->now_ms), 1);
  else p = put(p, "--:--:--");
  p = kv(p, " w=", (uint32_t)s->wid);
  p = kv(p, " v=", (uint32_t)s->votes);
  if (s->day) p = dec(put(p, " d="), s->day, 2), p = s->mon ? dec(put(p, "/"), s->mon, 2) : p;
  say(api, s, p);
  /* the radio's clock (API level 8): UTC now, within the interval's far end of the estimate */
  if (s->votes >= 2 && api->level >= 8) {
    uint32_t off = s->wid > 10000 || s->wid / 2 < 500 ? (uint32_t)s->wid / 2 : 500u;
    api->clock_set(utc(s, s->now_ms), KM_DATE(s->day, s->mon, s->yr ? 2000 + s->yr : 0),
                   (uint32_t)s->wid - off);
  }
}

/* a good frame: its source, and a timestamp if it has one */
static void frame(const k6_api_t *api, st_t *s) {
  const uint8_t *f = s->fr, *q;
  int n = s->len - 2, a, i, kind = 0, res = 0, day = 0, mon = 0, yr = 0;
  uint32_t tod = 0, t0;
  char *p;
  for (a = 0; a < n && !(f[a] & 1); a++) {} /* the address field ends at a byte with bit 0 set */
  /* after 2 to 10 whole addresses: a + 1 a multiple of 7 (times 7's inverse mod 2^32, no division) */
  if (a < 13 || a > 69 || (uint32_t)(a + 1) * 0xB6DB6DB7u > 0x24924924u || a >= n - 2 ||
      f[a + 1] != 0x03 || f[a + 2] != 0xF0)
    return;
  s->hops = 0;
  for (i = 20; i <= a; i += 7) s->hops += f[i] >> 7; /* digipeaters that have repeated it */
  q = f + a + 3, n -= a + 3;
  /* when its first bit went out: the bits since the opening flag at 1200 baud */
  t0 = s->now_ms - udiv((s->nbits + 8) * 5u, 6u);
  if (n >= 8 && (q[0] == '/' || q[0] == '@' || (q[0] == '>' && q[7] == 'z')))
    kind = q[7], q++;
  else if (n >= 18 && q[0] == ';' && (q[10] == '*' || q[10] == '_'))
    kind = q[17], q += 11;
  else if (n >= 9 && q[0] == '_') { /* _MMDDHHMM: the month, then a day-hour-minute */
    mon = d2(q + 1), q += 3, kind = 'w';
    if (mon < 1 || mon > 12) kind = mon = 0;
  }
  if (kind == 'z' || kind == 'h' || kind == 'w') {
    int32_t v = tri(q, kind == 'h' ? 24 : 32);
    day = d2(q);
    if (v < 0 || (kind != 'h' && !day)) kind = 0;
    else if (kind != 'h') tod = (uint32_t)(v - day * 3600) * 60000u, res = 60000; /* hour < 24 */
    else tod = (uint32_t)v * 1000u, res = 1000, day = 0;
    if (kind != 'h' && tod >= DAY) kind = 0;
    if (!kind) mon = 0;
  } else if (n >= 20 && q[0] == '$' && q[1] == 'G' && q[3] == 'R' && q[4] == 'M' && q[5] == 'C' &&
             q[6] == ',') {
    /* $GPRMC,hhmmss[.ss],A,lat,N,lon,E,speed,course,ddmmyy */
    const uint8_t *e = q + n, *r = q + 7;
    int32_t v = tri(r, 24);
    int fld = 1;
    kind = 0;
    if (v >= 0) {
      tod = (uint32_t)v * 1000u, res = 1000;
      if (r[6] == '.' && d2(r + 7) >= 0) tod += (uint32_t)d2(r + 7) * 10u, res = 10;
      for (; r < e && fld < 9; r++)
        if (*r == ',') {
          fld++;
          if (fld == 2 && r + 1 < e && r[1] != 'A') break;
          if (fld == 2) kind = 'r';
        }
      if (kind && fld == 9 && r + 6 <= e) {
        day = d2(r), mon = d2(r + 2), yr = d2(r + 4);
        if (day < 1 || day > 31 || mon < 1 || mon > 12 || yr < 0) day = mon = yr = 0;
      }
    }
  } else kind = 0;
  *kv(call(s->last, f + 7), " h", (uint32_t)s->hops) = 0;
  p = put(s->ln, "f ");
  p = call(p, f + 7);
  p = kv(p, " n=", (uint32_t)s->len);
  p = kv(p, " h=", (uint32_t)s->hops);
  if (kind) {
    p = put(p, " ts=");
    p = hms(p, tod, res < 1000);
    *p++ = (char)kind;
  }
  say(api, s, p);
  if (kind) add(api, s, f + 7, kind, tod, res, t0, day, mon, yr);
}

static int crc_ok(const uint8_t *b, int n) {
  uint32_t c = 0xFFFF;
  int i, j;
  for (i = 0; i < n; i++) {
    c ^= b[i];
    for (j = 0; j < 8; j++) c = (c >> 1) ^ (0x8408u & (0u - (c & 1)));
  }
  return c == 0xF0B8;
}

/* one bit out of the NRZI decoder */
static void hdlc(const k6_api_t *api, st_t *s, uint32_t bit) {
  uint32_t sr = s->sr = (s->sr << 1) | bit;
  if ((sr & 0xFF) == 0x7E) {
    s->flag_ms = s->now_ms;
    /* a preamble: flags back to back (7 bits since the last: its last 0 is this one's first).
       Noise makes a lone flag every second or so, four in a row hardly ever */
    s->run = s->in && s->nbits == 7 ? s->run + 1 : 0;
#ifndef APRS_FSK
    if (s->run == 3) s->pre++;
#endif
    if (s->in && s->len >= 18) {
      if (crc_ok(s->fr, s->len)) {
        s->ok++;
        if (api->level >= 7) api->led(1);
        frame(api, s);
#if !defined(APRS_HOST) && !defined(APRS_FSK)
        resync(s); /* the line took the CPU for a while */
#endif
      } else s->bad++;
    }
    s->in = 1, s->len = 0, s->bb = 0x80, s->nbits = 0;
    return;
  }
  if ((sr & 0x7F) == 0x7F) {
    s->in = 0;
    return;
  }
  if (!s->in) return;
  s->nbits++;
  if ((sr & 0x3F) == 0x3E) return; /* a stuffed zero */
  if (bit) s->bb |= 0x100;
  if (s->bb & 1) {
    if (s->len >= MAXF) s->in = 0;
    else s->fr[s->len++] = (uint8_t)(s->bb >> 1);
    s->bb = 0x80;
  } else s->bb >>= 1;
}

#ifndef APRS_FSK
static int32_t mag(int32_t a, int32_t b) {
  if (a < 0) a = -a;
  if (b < 0) b = -b;
  return a > b ? a + ((3 * b) >> 3) : b + ((3 * a) >> 3);
}

/* tone t's template at beta btab[t]: exp(j beta sin(2 pi i / 64)), its phase rounded to 1/64 turn */
static void gtab(st_t *s, int t) {
  int i, q;
  for (i = 0; i < GN; i++) {
    q = (s->btab[t] * SIN[i & 63] + 1024) >> 11; /* beta sin in 1/64 turn (127 * 16 ~ 2048) */
    s->g[t][i][0] = SIN[(q + 16) & 63], s->g[t][i][1] = SIN[q & 63];
  }
}

/* the deviation tracker: beta from the tone's discriminator level v (mag >> 11) where it won,
   a one-pole over 64 samples; the template follows when beta has moved half a step */
static void track(st_t *s, int t, int32_t v) {
  int32_t b = t ? (v * 45) >> 10 : (v * 41) >> 9; /* level -> beta: mark n / 12.5, space n / 22.8 */
  if (b < 64) b = 64;
  if (b > 768) b = 768;
  s->bq[t] += ((b << 6) - s->bq[t]) >> 6;
  b = s->bq[t] >> 6;
  if (b - s->btab[t] >= 8 || s->btab[t] - b >= 8) s->btab[t] = b, gtab(s, t);
}

#ifndef APRS_DISC
/* tone t's matched filter, one sample in: the IF sample (x, y) against the template at the
   tone's phase index i for 8 audio phases (each also stands for theta + pi). Spread over the
   samples, not once a bit: a burst of the whole window's work outruns the 0.2 ms DMA ring */
static void mfin(st_t *s, int t, int32_t x, int32_t y, uint32_t i) {
  const int8_t *g = s->g[t][i];   /* the table runs on past 64 (GN), so no wrap in here */
  int32_t *q = s->acc[t][0], *e = q + 4 * 8;
  for (; q < e; q += 4, g += 4 * 2) { /* 8 audio phases, 4 table steps apart */
    int32_t c = g[0], sn = g[1];
    q[0] += x * c, q[2] += y * c, q[1] += y * sn, q[3] += x * sn;
  }
}

/* the bit's metric for tone t: the largest |sum z conj(g)| over 16 audio phases; sums cleared */
static int32_t mfout(st_t *s, int t) {
  int32_t best = 0, m, *q;
  int p;
  for (p = 0; p < 8; p++) {
    q = s->acc[t][p];
    m = mag(q[0] + q[1], q[2] - q[3]); /* z conj(g): theta */
    if (m > best) best = m;
    m = mag(q[0] - q[1], q[2] + q[3]); /* z g: theta + pi */
    if (m > best) best = m;
    q[0] = q[1] = q[2] = q[3] = 0;
  }
  return best;
}
#endif

/* one complex sample at ~14.5 kHz */
static void audio(const k6_api_t *api, st_t *s, int32_t x, int32_t y) {
  int32_t ang = 0, d, v, m, sp, t, i, x0 = x, y0 = y;
  uint32_t prev, i1 = s->ph1 >> 26, i2 = s->ph2 >> 26;
  /* CORDIC vectoring: the phase in 2^16 a turn */
  if (x < 0) x = -x, y = -y, ang = 32768;
  for (i = 0; i < 11; i++) {
    t = x;
    if (y > 0) x += y >> i, y -= t >> i, ang += ATN[i];
    else x -= y >> i, y += t >> i, ang -= ATN[i];
  }
  d = (int16_t)(uint16_t)(ang - s->ang); /* the FM discriminator: the phase step */
  s->ang = ang;
  d -= s->dc >> 8;
  s->dc += d;
  /* sliding correlators over the last n samples */
  {
    int c1 = SIN[((s->ph1 >> 26) + 16) & 63], s1 = SIN[s->ph1 >> 26];
    int c2 = SIN[((s->ph2 >> 26) + 16) & 63], s2 = SIN[s->ph2 >> 26];
    int32_t *r0 = s->cr[0], *r1 = s->cr[1], *r2 = s->cr[2], *r3 = s->cr[3];
    int w = s->w, o = (w - s->n) & (NW - 1);
    s->ph1 += s->st1, s->ph2 += s->st2;
    r0[w] = d * c1, r1[w] = d * s1, r2[w] = d * c2, r3[w] = d * s2;
    s->sum[0] += r0[w] - r0[o];
    s->sum[1] += r1[w] - r1[o];
    s->sum[2] += r2[w] - r2[o];
    s->sum[3] += r3[w] - r3[o];
    s->w = (w + 1) & (NW - 1);
  }
  m = mag(s->sum[0], s->sum[1]) >> 11;
  sp = mag(s->sum[2], s->sum[3]) >> 11;
  /* each tone's deviation, where the discriminator says that tone */
  if (m > sp) track(s, 0, m);
  else track(s, 1, sp);
  {
#ifndef APRS_DISC
    /* the IF turned back by the carrier offset (the discriminator's DC), into each tone's sums */
    uint32_t r = (s->rot += (uint32_t)(s->dc >> 8)) >> 10;
    int c = SIN[(r + 16) & 63], sn = SIN[r & 63];
    int32_t xr = (x0 * c + y0 * sn) >> 7, yr = (y0 * c - x0 * sn) >> 7;
    mfin(s, 0, xr, yr, i1);
    mfin(s, 1, xr, yr, i2);
#else
    (void)x0, (void)y0, (void)i1, (void)i2;
#endif
  }
  /* mark against space on the discriminator, as they come: its transitions keep the bit clock
     (a peak tracker on each tone, for a sender's pre-emphasis twist, lost frames at +6 dB and
     gained nothing elsewhere: host sweep) */
  v = m > sp;
#ifdef APRS_TRACE
  APRS_TRACE(s, d, m, sp);
#endif
  /* the bit clock: sample as the phase passes half a turn, pulled towards each transition */
  prev = s->pll;
  s->pll += s->stb;
#ifdef APRS_DISC
  if ((int32_t)prev >= 0 && (int32_t)s->pll < 0) {
    hdlc(api, s, v == s->lastbit);
    s->lastbit = v;
  }
#else
  /* the bit decided by the matched filter over the samples since the last: the
     discriminator's sliding window puts its transitions (where they pull the clock) half a
     window late, so the clock's half turn is the bit's end */
  if ((int32_t)prev >= 0 && (int32_t)s->pll < 0) {
    int b = mfout(s, 0) > mfout(s, 1);
    hdlc(api, s, b == s->mlast);
    s->mlast = b;
  }
#endif
  if (v != s->lastv) {
    int32_t p = (int32_t)s->pll;
    s->pll = (uint32_t)(s->in ? p - (p >> PLL_IN) : p >> 1);
    s->lastv = v;
  }
}

/* one count (2 * ones - 64) through the mixer and CIC */
static void count(const k6_api_t *api, st_t *s, int c) {
  uint32_t ix = s->ph >> 26;
  s->ph += s->dph;
  s->i1 += c * SIN[(ix + 16) & 63], s->i2 += s->i1;
  s->q1 -= c * SIN[ix], s->q2 += s->q1;
  if (++s->k == DEC) {
    int32_t a = s->i2 - s->ci, b = s->q2 - s->cq, x = a - s->di, y = b - s->dq;
    s->ci = s->i2, s->cq = s->q2, s->di = a, s->dq = b, s->k = 0;
    audio(api, s, x >> 4, y >> 4);
  }
}

#ifndef APRS_HOST
/* every whole count the DMA has made, through the mixer and CIC */
static void drain(const k6_api_t *api, st_t *s) {
  uint32_t P = poll(s);
  if (P - s->consumed > RSZ - 64) {
    s->lost++;
    resync(s);
    return;
  }
  /* s->produced, not P: a frame's line resyncs the ring from inside count() */
  while (s->produced - s->consumed >= 64) {
    const uint8_t *q = s->ring + (s->consumed & (RSZ - 1));
    s->consumed += 64; /* before count(): its resync sets it */
    count(api, s, 2 * ones64(q) - 64);
  }
}

static void dma_on(uint8_t *ring) {
  wr(DMA_CCR1, 0);
  wr(DMA + 0x04, 0xF);
  wr(DMA_CPAR1, GPIOB + 0x10);
  wr(DMA_CMAR1, (uint32_t)ring);
  wr(DMA_CNDTR1, RSZ);
  wr(DMA_CCR1, (3u << 12) | (1u << 7) | (1u << 5) | 1u);
}
#endif /* APRS_HOST */
#endif /* APRS_FSK */

#ifndef APRS_HOST

static void screen(const k6_api_t *api, st_t *s) {
  char *p;
  p = dec(put(s->ln, " APRS CLOCK "), udiv(APRS_HZ, 1000000), 3);
  *p++ = '.';
  p = dec(p, udiv(APRS_HZ, 1000) - 1000 * udiv(APRS_HZ, 1000000), 3);
  *p = 0;
  api->lcd_text(0, s->ln);
  p = put(s->ln, "UTC ");
  if (s->votes >= 2) p = hms(p, utc(s, s->now_ms), 0);
  else p = put(p, "--:--:--");
  if (s->day) { /* "UTC hh:mm:ss dd/mm/yy": 21 characters, the screen's width */
    p = dec(put(p, " "), s->day, 2);
    if (s->mon) p = dec(put(p, "/"), s->mon, 2), p = dec(put(p, "/"), s->yr, 2);
  }
  *p = 0;
  api->lcd_text(1, s->ln);
  p = kv(s->ln, "stations ", (uint32_t)s->votes);
  if (s->votes >= 2) p = put(p, s->wid < 30000 ? " (sec)" : " (min)");
  *p = 0;
  api->lcd_text(2, s->ln);
  /* packets heard starting (preambles), then decoded: noise makes "bad" runs all the time, so
     the gap between these two is the real misses */
  p = kv(s->ln, "heard ", s->pre);
  p = kv(p, " ok ", s->ok);
  p = kv(p, " ts ", s->nts);
  *p = 0;
  api->lcd_text(3, s->ln);
  if (s->ok) { /* the station heard last: "last VE7ABC-15 h2" */
    *put(put(s->ln, "last "), s->last) = 0;
    api->lcd_text(4, s->ln);
  }
  api->lcd_text(5, "any key: done");
  api->lcd_flush();
}

#endif /* APRS_HOST */

#if !defined(APRS_HOST) && !defined(APRS_FSK)
int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, apb2, ahb, i, tkey = 0;
  uint16_t r33, r34, r7e, r13;
  char *p;
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(st_t) / 4; i++) ((uint32_t *)s)[i] = 0;
  if (rd(GPIOB) & (3u << 2)) return -4;
  api->tune(APRS_HZ, 1);
  api->delay_us(50000);
  apb2 = rd(RCC_APB2ENR), ahb = rd(RCC_AHBENR);
  wr(RCC_AHBENR, ahb | 1u);
  if ((apb2 & TIM17EN) || (rd(DMA_CCR1) & 1)) {
    wr(RCC_AHBENR, ahb);
    return -7;
  }
  r33 = api->fd_read(0x33), r34 = api->fd_read(0x34);
  if ((r33 & 0x0100) && api->fd_write(0x33, (uint16_t)(r33 & ~0x0100)) != 0) {
    wr(RCC_AHBENR, ahb);
    return -5;
  }
  api->fd_write(0x34, (uint16_t)((r34 & 0xFFF0) | 13));
  /* The gain frozen at its maximum, as the AIS and DMR modules do: the 1-bit tap's own noise
     lies above the receiver's even there, so wherever the AGC turns the IF down (a strong
     neighbour, a big antenna's whole band) a weak packet sinks further under it
     (docs/k6-dmr.md: +10 dB over the tap's noise at 0x03FF against +4 to +5 with the AGC). */
  r7e = api->fd_read(0x7E), r13 = api->fd_read(0x13);
  if (GAIN13) {
    api->fd_write(0x7E, (uint16_t)((r7e & 0x0FFF) | 0x8000 | (3u << 12)));
    api->fd_write(0x13, GAIN13);
  }
  s->rlo = 0x1FF;
  wr(RCC_APB2ENR, apb2 | TIM17EN);
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x28, 0);
  wr(TIM17 + 0x2C, 0xFFFF);
  wr(TIM17 + 0x14, 1);
  wr(TIM17 + 0x10, 0);
  wr(TIM17 + 0x0C, 1u << 8);
  wr(TIM17 + 0x00, 1);
  s->c17 = rd(TIM17_CNT) & 0xFFFF, s->c14 = rd(TIM14_CNT) & 0xFFFF;
  dma_on(s->ring);
  /* the DMA's rate over about 50 ms */
  resync(s);
  while (poll(s) - s->p0 < 256u * 1024u) {}
  now(s);
  s->cps8 = muldiv(s->t - s->t0, 256, s->produced - s->p0);
  steps(s);
  s->n = (int32_t)udiv(64000000u + 600u * s->cps8, 1200u * s->cps8); /* fa / 1200, rounded */
  if (s->n > NW - 1) s->n = NW - 1;
  p = put(s->ln, "aprs start hz=");
  p = dec(p, APRS_HZ, 1);
  p = kv(p, " ks=", udiv(96000u * 256u, s->cps8));
  p = kv(p, " n=", (uint32_t)s->n);
  p = kv(p, " g=", GAIN13);
  say(api, s, p);
  resync(s);
  for (;;) {
    drain(api, s);
    if ((int32_t)(s->now_ms - tkey) >= 50) {
      int key;
      s->now_ms = api->ms();
      tkey = s->now_ms;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key || s->now_ms >= RUN_MS) break;
      if (api->level >= 7 && s->now_ms - s->flag_ms > 120) api->led(0);
      /* the screen takes 20-40 ms (the ring holds 0.2 ms): once a second, and never while a
         frame or its flags are coming (the HDLC is inside a frame: up to 2.3 s since its flag) */
      if (s->now_ms - s->scr_ms >= 1000 && s->now_ms - s->flag_ms > 100 &&
          !(s->in && s->now_ms - s->flag_ms < 2400)) {
        s->scr_ms = s->now_ms;
        /* the DMA's rate again over the stretch since the last pause (it moves with the CPU's
           load): about 0.2 ms of sums, so here, where a pause costs nothing */
        if (s->consumed - s->p0 > (1u << 22)) {
          uint32_t c;
          now(s);
          c = muldiv(s->t - s->t0, 256, s->consumed - s->p0);
          /* a stretch past TIM14's 6.5 s wrap reads wrong: only a rate within 1/16 */
          if (c - s->cps8 + (s->cps8 >> 4) <= (s->cps8 >> 3)) s->cps8 = (3 * s->cps8 + c) >> 2, steps(s);
        }
        {
          /* the level, here once a second where a pause costs nothing: the minute's least is
             the floor, its most a packet's level if one was on the air at a look (a packet
             being decoded puts the look off, so pk is mostly the ones that didn't decode) */
          uint16_t r = api->fd_read(0x67) & 0x1FF;
          if (r < s->rlo) s->rlo = r;
          if (r > s->rhi) s->rhi = r;
        }
        if (s->now_ms - s->min_ms >= 60000) { /* once a minute: the channel, for the bench */
          s->min_ms = s->now_ms;
          p = kv(s->ln, "m ", udiv(s->now_ms, 60000));
          p = put(p, " rssi=-");
          p = dec(p, (uint32_t)-KM_RSSI_DBM(s->rlo), 1);
          p = put(p, " pk=-");
          p = dec(p, (uint32_t)-KM_RSSI_DBM(s->rhi), 1);
          p = kv(p, " pre=", s->pre);
          p = kv(p, " ok=", s->ok);
          say(api, s, p);
          s->rlo = 0x1FF, s->rhi = 0;
        }
        if (api->level >= 6) screen(api, s);
        resync(s);
      }
    } else s->now_ms = api->ms();
  }
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x0C, 0);
  wr(DMA_CCR1, 0);
  wr(DMA + 0x04, 0xF);
  wr(DMA_CNDTR1, 0);
  wr(DMA_CPAR1, 0);
  wr(DMA_CMAR1, 0);
  wr(RCC_APB2ENR, apb2);
  wr(RCC_AHBENR, ahb);
  if (GAIN13) api->fd_write(0x13, r13), api->fd_write(0x7E, r7e);
  api->fd_write(0x34, r34);
  api->fd_write(0x33, r33);
  p = put(s->ln, "aprs end ok=");
  p = dec(p, s->ok, 1);
  p = kv(p, " bad=", s->bad);
  p = kv(p, " ts=", s->nts);
  p = kv(p, " v=", (uint32_t)s->votes);
  if (s->votes >= 2) p = hms(put(p, " utc="), utc(s, s->now_ms), 1);
  p = kv(p, " lost=", s->lost);
  p = kv(p, " pre=", s->pre);
  say(api, s, p);
  if (!s->ok && s->now_ms < RUN_MS) return -2;
  return s->votes;
}
#endif

#if defined(APRS_FSK) && !defined(APRS_HOST)
/* ---- the chip's own FSK modem as the front end (docs/k6-aprs-clock.md, probes 1 to 4) ----

   The GP0 tap's own noise puts the receiver above some 20 dB short of the chip's. The chip's
   packet modem sits behind its own FM demodulator, and its 1200/2400 Hz receiver with
   REG_58<7:6> = 10 (0x33A3) at 1200 baud slices Bell 202 bit for bit (probe 4: 9 frames of 9).
   An APRS packet opens with flags, which that receiver reads as FE FE FE (01 01 01 with the
   tones the other way round; the chip takes its sync word either way up), so the sync word is
   four flags (aprsfskb: two, which noise also gives several times a second). From the sync
   flag (16 bits after the word, the first FIFO word in) the words are read by time, a word
   behind the chip, each bit NRZI-decoded into hdlc() with its own time, until the line has
   been outside a frame for FSK_IDLE bits or the chip's 256 bytes (1.7 s) are up; then the
   modem is turned off and on to listen for the next sync word.

   What the second bench run showed (42 packets of 42 on 440.850): turning the modem on raises
   the sync flag by itself within a few ms, so every search opens with a read of three stale
   words (the empty FIFO gives its last word again) that the idle line ends, 59 ms a round; a
   packet's real words then arrive inside such a read and decode (40 of the 42 came that way).
   So the reads need no sync flag at all. FSK_STREAM (aprsfskb) is that taken at its word: the
   FIFO is read all the time, a word every 40/3 ms, and a word that is not the last one again
   means the chip has found its sync word. hdlc() starts as it is after a flag, because the
   sync word was four of them and may have been the opening's last four (a real packet on
   144.390 was lost that way: its first FIFO word was the frame's own start). The bits go to
   hdlc() until the line has been idle for FSK_IDLE bits or the chip says RX done; then the
   modem is turned off and on. With nothing heard the search is started again every FSK_AGAIN
   ms, whatever state the chip is in: one turning off and on does not always end a packet the
   chip is in, and at 2 s the packet after a packet was lost on the bench (1 of 7); at 59 ms,
   aprsfsk's own round, none was (the fourth bench run, 2026-11-10).

   Lines, besides the tap build's f, clk and m lines (pre = sync words heard):
     aprs start hz=144390000 fsk=13219 sync=248         (REG_58, REG_59; FSK_STREAM: " stream" after)
     y 12 t=123456 w=37 ok=1 st=0002.c9a2   a packet: its number, ms at the sync, words read,
                                        good frames in it, REG_02's flags and REG_0B then
                                        (FSK_STREAM: the flags since 20 ms after the search began)
     d 12 000 fefe fe01 ...             its words, 10 a line (APRS_FSK_DUMP 2: every packet; 1:
                                        those without a good frame; 0: none)
     s 3 t=28924 cfg=3333 ok=12               FSK_SWEEP: the setting from here on (its number,
                                        REG_58) and the good frames so far
     aprs fsk ms=900004 key=0 pre=31 fake=912   before the end line: the module's clock, the key
                                        that ended it, sync words, those of under 6 words
   Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 3F, 02 and (FSK_AF) 2B, put back as read (the pager arms its
   own receiver again after any module). Returns as the tap build does, less -4, -5 and -7. */
#define FSK_CFG 0x33A3u  /* REG_58: the 1200/2400 Hz receiver, bits 7:6 = 10 */
#if defined(FSK_PLAIN) && !defined(FSK_CTRL)
#define FSK_CTRL 0x00F0u /* REG_59: REG_5A alone, a sync word of two bytes */
#endif
#ifndef FSK_CTRL
#define FSK_CTRL 0x00F8u /* REG_59: REG_5A then REG_5B, four flags (00F0: REG_5A alone, two) */
#endif
#ifndef APRS_FSK_DUMP
#define APRS_FSK_DUMP 1
#endif
#define FSK_SYNC 0xFEFEu
#define FSK_WORDS 128    /* REG_5D's most: 256 bytes */
#define FSK_LAG 1        /* words the timed reads stay behind the chip (its FIFO holds 8) */
#define FSK_IDLE 40      /* line bits outside a frame: the packet is over */
#define FSK_SETTLE 100u  /* ms after the modem is turned on: an RX-done in these is its own */
#ifndef FSK_AGAIN
#define FSK_AGAIN 59u    /* ms with nothing heard: the search is started again */
#endif
#define NSV 10

static const uint8_t FSK_SAVED[NSV] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x3F, 0x2B};

#ifdef FSK_SWEEP
/* a setting every FSK_SWEEP ms, round and round: which REG_58 reads a pre-emphasised packet?
   The fourth bench run went round bits 7:6, the bandwidths, the RX gain and REG_2B's
   de-emphasis: 33a3, 33a1 and 31a3 read the flat and the de-emphasised kinds, 3323 and 33e3
   only the de-emphasised, 3363 and 33a9 nothing, REG_2B + 0100 changed nothing, and none read
   the pre-emphasised. Bits 7:6 lean the slicer, so here all sixteen of bits 7:4. */
#ifndef FSK_SW_CFG
#define FSK_SW_CFG(n) (0x3303u | (n) << 4)
#define FSK_NSW 16u
#endif
#endif

static void fsk_arm(const k6_api_t *api) {
  api->fd_write(0x59, FSK_CTRL | 0x4000);
  api->fd_write(0x59, FSK_CTRL | 0x1000);
  api->fd_write(0x02, 0);
}

/* Listen for the next sync word, from wherever the chip is. Arming alone does not end a packet
   the chip is in (the first bench run: it read on to its 256 bytes, deaf, and said so only with
   its RX-done), so the modem is turned off and on, and armed twice as at the start. */
static void fsk_seek(const k6_api_t *api, uint32_t cfg) {
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x58, (uint16_t)cfg);
  fsk_arm(api);
  api->delay_us(2000);
  fsk_arm(api);
}

#ifndef FSK_PLAIN
static char *hex4(char *p, uint32_t v) {
  int i;
  for (i = 12; i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}

/* a packet's lines: y, and its words */
static void fsk_said(const k6_api_t *api, st_t *s, uint32_t tsync, uint32_t got, uint32_t ok,
                     uint32_t st) {
  uint32_t i;
  char *p;
  /* a line costs tens of ms of listening: none for a sync word found in noise (a few words,
     no frame), which a quiet channel gives all the time */
  if (APRS_FSK_DUMP < 2 && !ok && got < 6) return;
  p = kv(s->ln, "y ", s->pre);
  p = kv(p, " t=", tsync);
  p = kv(p, " w=", got);
  p = kv(p, " ok=", ok);
  p = hex4(put(p, " st="), st >> 16);
  *p++ = '.', p = hex4(p, st & 0xFFFF);
  say(api, s, p);
  if (APRS_FSK_DUMP < 2 && (!APRS_FSK_DUMP || ok)) return;
  for (i = 0; i < got; i++) {
    uint32_t col = i - 10 * udiv(i, 10);
    if (!col) p = dec(put(kv(s->ln, "d ", s->pre), " "), i, 3);
    *p++ = ' ', p = hex4(p, s->cap[i]);
    if (col == 9 || i + 1 == got) say(api, s, p);
  }
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now, tkey = 0, tsync = 0, idle = 0, ok0 = 0, st = 0, left = 0, got = 0;
#ifdef FSK_SWEEP
  uint32_t cfg = FSK_SW_CFG(0), sw = 0, tsw = 0;
#else
  uint32_t cfg = FSK_CFG;
#endif
#ifdef FSK_STREAM
  uint32_t acc = 0, tlast, tseek = 0, prev = 0;
#else
  uint16_t tw = 0;
#endif
  int key = 0;
  char *p;
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(st_t) / 4; i++) ((uint32_t *)s)[i] = 0;
  for (i = 0; i < NSV; i++) s->sv[i] = api->fd_read(FSK_SAVED[i]);
  api->tune(APRS_HZ, 1);
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
#ifdef FSK_AF
  /* REG_2B's filters are in the modem's path (probe 3): 0100 the de-emphasis off */
  api->fd_write(0x2B, s->sv[9] | FSK_AF);
#endif
  api->fd_write(0x5A, FSK_SYNC);
  api->fd_write(0x5B, FSK_SYNC);
  api->fd_write(0x72, 0x3065);  /* 1200 baud x 10.32444 */
  api->fd_write(0x5C, 0x5625);  /* no CRC of the chip's */
  api->fd_write(0x5D, (2 * FSK_WORDS - 1) << 8);
  api->fd_write(0x5E, 0x0204);  /* RX almost full at 4 words */
  fsk_seek(api, cfg);
  api->fd_write(0x3F, 0x3002);  /* RX done, almost full, sync found: as the probes had it */
#ifdef FSK_STREAM
  for (i = 0; i < 8; i++) prev = api->fd_read(0x5F);
  prev = (prev & 0xFF) << 8 | prev >> 8;
  tlast = api->ticks() & 0xFFFF;
#endif
  s->rlo = 0x1FF;
  p = put(s->ln, "aprs start hz=");
  p = dec(p, APRS_HZ, 1);
  p = kv(p, " fsk=", FSK_CFG);
  p = kv(p, " sync=", FSK_CTRL);
#ifdef FSK_STREAM
  p = put(p, " stream");
#endif
  say(api, s, p);
  for (;;) {
    now = api->ms();
    if (now - tkey >= 50) {
      uint16_t r = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      if (r < s->rlo) s->rlo = r;
      if (r > s->rhi) s->rhi = r;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key || now >= RUN_MS) break;
      if (api->level >= 7 && now - s->flag_ms > 120) api->led(0);
    }
#ifdef FSK_SWEEP
    if (!left && now - tsw >= FSK_SWEEP) {
      tsw += FSK_SWEEP;
      if (++sw == FSK_NSW) sw = 0;
      cfg = FSK_SW_CFG(sw);
      fsk_seek(api, cfg);
      p = kv(s->ln, "s ", sw);
      p = kv(p, " t=", now);
      p = hex4(put(p, " cfg="), cfg);
      p = kv(p, " ok=", s->ok);
      say(api, s, p);
    }
#endif
#ifdef FSK_STREAM
    {
      uint32_t f = api->fd_read(0x02) & 0x3002, tk = api->ticks() & 0xFFFF, n, over = 0;
      if (f) {
        api->fd_write(0x02, 0);
        if (now - tseek >= 20) st |= f << 16; /* not the sync flag that turning it on raises */
      }
      acc += ((tk - tlast) & 0xFFFF) * 3, tlast = tk;
      n = udiv(acc, 400), acc -= 400 * n; /* a word is 40/3 ms */
      if (n > 8) n = 8;                   /* the FIFO's size */
      while (n && !over) {
        /* its low byte was on the air first (the pager's swap), each byte's top bit first */
        uint32_t w = api->fd_read(0x5F), v = (w & 0xFF) << 8 | w >> 8;
        n--;
        if (!left) {
          if (v == prev) continue; /* the empty FIFO: its last word again */
          left = 1, got = 0, idle = 0, tsync = now, ok0 = s->ok;
          st = (st & 0xFFFF0000) | api->fd_read(0x0B);
          /* the sync word was four flags and this is the bit after them, so a frame may begin
             right here: hdlc() as it is after a flag. That bit is a 0 (a frame's first, or
             the next flag's), a change of tone, which gives the tone before it */
          s->in = 1, s->len = 0, s->bb = 0x80, s->nbits = 0, s->run = 0, s->sr = 0x7E;
          s->mlast = (int32_t)(~v >> 15 & 1);
        }
        prev = v;
        if (got < FSK_WORDS) s->cap[got] = (uint16_t)v;
        got++;
        s->now_ms = now > 13 * n ? now - 13 * n : now; /* words still to read came later */
        for (i = 16; i--;) {
          int32_t tone = (int32_t)((v >> i) & 1);
          hdlc(api, s, tone == s->mlast);
          s->mlast = tone;
          idle = s->in ? 0 : idle + 1;
        }
        over = idle >= FSK_IDLE || got >= FSK_WORDS;
      }
      if ((f & 0x2000) && now - tseek >= FSK_SETTLE) {
        /* the chip's 256 bytes are up; with no word read, a packet nobody saw */
        if (!left) s->lost++;
        over = 1;
      }
      if (!left && now - tseek >= FSK_AGAIN) over = 1;
      if (over) {
        fsk_seek(api, cfg);
        for (i = 0; i < 8; i++) prev = api->fd_read(0x5F); /* what is left in the FIFO */
        prev = (prev & 0xFF) << 8 | prev >> 8;
        if (left) {
          s->pre++;
          if (got < 6 && s->ok == ok0) s->fake++;
          fsk_said(api, s, tsync, got < FSK_WORDS ? got : FSK_WORDS, s->ok - ok0, st);
        }
        left = 0, st = 0, acc = 0, tlast = api->ticks() & 0xFFFF, tseek = api->ms();
      }
    }
    if (!left && now - s->scr_ms >= 1000) {
#else
    {
      /* the flags themselves, not REG_0C's "interrupt pending": the first bench run got that
         for most packets only with their RX-done */
      uint32_t f = api->fd_read(0x02) & 0x3002;
      if (f) api->fd_write(0x02, 0);
      if (f && !left) {
        if (f & 0x2000) {
          /* the chip has read a whole packet nobody took: its words are gone. Start again */
          s->lost++;
          fsk_seek(api, cfg);
        } else {
          /* sync found: the first word is in. Almost full as well (this loop was away, saying
             a line or drawing): so are three more */
          uint32_t late = f & 0x1000 ? 40 : 0; /* ms */
          tw = (uint16_t)(api->ticks() - 10 * late); /* 100 us ticks, a 16-bit count */
          st = api->fd_read(0x0B) | f << 16;
          left = FSK_WORDS, got = 0, idle = 0, tsync = now - late, ok0 = s->ok;
          s->in = 0, s->sr = 0, s->pre++;
        }
      }
    }
    if (left) {
      /* a word is 40/3 ms: those the chip has had for FSK_LAG word times */
      uint32_t due = udiv(((api->ticks() - tw) & 0xFFFF) * 3, 400);
      due = due > FSK_LAG ? due - FSK_LAG : 0;
      if (due > left) due = left;
      while (got < due && idle < FSK_IDLE) {
        /* its low byte was on the air first (the pager's swap), each byte's top bit first */
        uint32_t w = api->fd_read(0x5F), v = (w & 0xFF) << 8 | w >> 8;
        s->cap[got] = (uint16_t)v;
        for (i = 16; i--;) {
          int32_t tone = (int32_t)((v >> i) & 1);
          /* this bit's end: the word's first bit began 16 bits before the sync interrupt */
          s->now_ms = tsync + udiv((16 * got + 16 - i) * 5, 6) - 13;
          hdlc(api, s, tone == s->mlast);
          s->mlast = tone;
          idle = s->in ? 0 : idle + 1;
        }
        got++;
      }
      if (got >= left || idle >= FSK_IDLE) {
        fsk_seek(api, cfg);
        left = 0;
        if (got < 6 && s->ok == ok0) s->fake++;
        fsk_said(api, s, tsync, got, s->ok - ok0, st);
      }
    } else if (now - s->scr_ms >= 1000) {
#endif
      /* the minute's line and the screen (20-40 ms), only between packets */
      s->now_ms = s->scr_ms = now;
      if (now - s->min_ms >= 60000) {
        s->min_ms = now;
        p = kv(s->ln, "m ", udiv(now, 60000));
        p = put(p, " rssi=-");
        p = dec(p, (uint32_t)-KM_RSSI_DBM(s->rlo), 1);
        p = put(p, " pk=-");
        p = dec(p, (uint32_t)-KM_RSSI_DBM(s->rhi), 1);
        p = kv(p, " pre=", s->pre);
        p = kv(p, " ok=", s->ok);
        say(api, s, p);
        s->rlo = 0x1FF, s->rhi = 0;
      }
      if (api->level >= 6) screen(api, s);
    }
    api->delay_us(2000);
  }
  s->now_ms = now;
  for (i = 0; i < NSV; i++) api->fd_write(FSK_SAVED[i], s->sv[i]);
  api->fd_write(0x02, 0);
  p = kv(s->ln, "aprs fsk ms=", now); /* the end line is full: 60 characters a line */
  p = kv(p, " key=", (uint32_t)key);
  p = kv(p, " pre=", s->pre);
  p = kv(p, " fake=", s->fake);
  say(api, s, p);
  p = put(s->ln, "aprs end ok=");
  p = dec(p, s->ok, 1);
  p = kv(p, " bad=", s->bad);
  p = kv(p, " ts=", s->nts);
  p = kv(p, " v=", (uint32_t)s->votes);
  if (s->votes >= 2) p = hms(put(p, " utc="), utc(s, now), 1);
  p = kv(p, " lost=", s->lost);
  say(api, s, p);
  if (!s->ok && now < RUN_MS) return -2;
  return s->votes;
}
#else /* FSK_PLAIN */
/* ---- the chip's plain FSK receiver as a sampler of the audio (aprsdfsk.c) ----

   The 1200/2400 Hz receiver above reads Bell 202 only when it is flat and clean: no packet
   with a sender's pre-emphasis in four bench runs. The plain FSK receiver (REG_58 = 00E9) at
   4800 baud slices the audio itself, and its bit clock follows the zero crossings (dfskcap's
   bench captures, 2026-11-10): 2200 Hz comes as alternating bits, a sample a half cycle,
   1200 Hz as 0011, two samples a half cycle. So the samples between two transitions say the
   tone, whichever is the louder. df_sample() sums them into runs (units of 1/44 bit: a half
   cycle of space is 12, of mark 22) and rounds a run to line bits; the half cycle at a change
   of tone goes to either side, so a mark run is counted together with the space run after it.
   modem/aprs.py's dfsk_line is the same in Python and reads every clean packet of those
   captures (flat, pre-emphasised, de-emphasised, weak, eight opening flags) and none of the
   noisy ones.

   The chip starts on a sync word of 16 alternating bits (AAAA: 2200 Hz for 3.6 ms, in any
   opening of flags) and then gives up to 2048 bytes (REG_5D = FFE0), 3.4 s. Its RX-done flag
   means nothing here (it comes with the first word). The FIFO's words are taken
     DF_MODE 0: four at a time when REG_02 says almost full (REG_5E = 0204); with no such flag
                for DF_LATE ms four are tried anyway, and a word that is the one eight before
                (what the empty FIFO gives) ends the try;
     DF_MODE 1: by time, a word every 16/4800 s as dfskcap did. The chip's clock is up to a
                tenth slower than that, so about every eleventh read finds the FIFO empty and
                gets the word of eight reads before: dropped (modem/aprs.py dfsk_destale);
     DF_MODE 2: one, then the other, DF_SWAP ms each, with an s line (the bench's comparison).
   A packet is over when the line has been outside a frame for FSK_IDLE bits, when there has
   been no transition for DF_QUIET samples, or at DF_WORDS words; then the modem is turned off
   and on. With nothing heard the search is started again every FSK_AGAIN ms, as aprsfskb.

   Lines, besides the tap build's f, clk and m lines:
     aprs start hz=144390000 fsk=233 sync=240 plain
     s 1 t=40012 ok=31                  DF_MODE 2: the way of reading from here on, good frames so far
     y 12 t=123456 w=186 ok=1 e=0 m=0   a packet of 40 words or more, or with a frame: its number,
                                        ms at its first word, words, good frames, words that
                                        were the one eight before (DF_MODE 1: dropped), the mode
     aprs fsk ms=900004 key=0 pre=31 fake=912 late=0
   Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 3F and 02, put back as read. Receive only. */
#define DF_CFG 0x00E9u
#ifndef DF_MODE
#define DF_MODE 0
#endif
#ifndef DF_SWAP
#define DF_SWAP 48204u   /* five rounds of the bench's kinds of packet */
#endif
#define DF_THR 4u        /* REG_5E: almost full at this many words */
#define DF_LATE 20u      /* ms without the almost-full flag in a packet (four words are 13 to 15) */
#define DF_WORDS 1000u   /* the chip's packet is 1024 words */
#define DF_T 44          /* a line bit, in the slicer's units */
#define DF_SP 12         /* an interval of one sample: a half cycle of 2200 Hz */
#define DF_MK 22         /* of two: a half cycle of 1200 Hz */
#define DF_BIAS 3
#define DF_IDLE 8        /* samples without a transition: no tone of Bell 202 */
#define DF_QUIET 64      /* and this many: the packet is over */
#define DF_SAY 40u       /* words: a shorter packet without a frame is noise, and gets no line */

static void df_bits(const k6_api_t *api, st_t *s, int32_t tone, int32_t n) {
  while (n-- > 0) {
    hdlc(api, s, tone == s->mlast);
    s->mlast = tone;
    s->idle = s->in ? 0 : s->idle + 1;
  }
}

/* a run of one tone ends: a space run goes out, and before it the mark run held back */
static void df_end(const k6_api_t *api, st_t *s) {
  int32_t ns, nm;
  if (s->dtone) {
    s->dmd = s->ddur;
    return;
  }
  ns = (int32_t)udiv((uint32_t)s->ddur + DF_T / 2, DF_T);
  if (ns < 1) ns = 1;
  if (s->dmd >= 0) {
    nm = (int32_t)udiv((uint32_t)(s->dmd + s->ddur) + DF_BIAS + DF_T / 2, DF_T) - ns;
    df_bits(api, s, 1, nm < 1 ? 1 : nm);
    s->dmd = -1;
  }
  df_bits(api, s, 0, ns);
}

/* one bit of the chip's */
static void df_sample(const k6_api_t *api, st_t *s, int32_t b) {
  int32_t t, d;
  if (b == s->dlast) {
    if (++s->dn == DF_IDLE) { /* silence: what is held goes out, a mark run by itself */
      df_end(api, s);
      if (s->dmd >= 0) {
        d = (int32_t)udiv((uint32_t)s->dmd + DF_BIAS + DF_T / 2, DF_T);
        df_bits(api, s, 1, d < 1 ? 1 : d);
        s->dmd = -1;
      }
      s->dtone = 1, s->ddur = 0;
    }
    return;
  }
  if (s->dn < DF_IDLE) {
    t = s->dn >= 2;
    d = s->dn == 1 ? DF_SP : s->dn == 2 ? DF_MK : s->dn * (DF_T / 4);
    if (t == s->dtone) s->ddur += d;
    else df_end(api, s), s->dtone = t, s->ddur = d;
  }
  s->dlast = b, s->dn = 1;
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now, tkey = 0, tsync = 0, ok0 = 0, got = 0, eq = 0, late = 0, prev = 0;
  uint32_t tseek = 0, tfull = 0, tlast, rn = 0, since = 0;
#if DF_MODE == 2
  uint32_t tsw = 0;
#endif
  int32_t acc = 0;
  int key = 0, left = 0, mode = DF_MODE & 1;
  char *p;
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(st_t) / 4; i++) ((uint32_t *)s)[i] = 0;
  for (i = 0; i < NSV; i++) s->sv[i] = api->fd_read(FSK_SAVED[i]);
  api->tune(APRS_HZ, 1);
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x5A, 0xAAAA);
  api->fd_write(0x5B, 0xAAAA);
  api->fd_write(0x72, 0xC195);  /* 4800 baud x 10.32444 */
  api->fd_write(0x5C, 0x5625);  /* no CRC of the chip's */
  api->fd_write(0x5D, 0xFFE0);  /* 2048 bytes */
  api->fd_write(0x5E, 0x0200 | DF_THR);
  fsk_seek(api, DF_CFG);
  for (i = 0; i < 8; i++) prev = api->fd_read(0x5F);
  prev = (prev & 0xFF) << 8 | prev >> 8;
  tlast = api->ticks() & 0xFFFF;
  s->rlo = 0x1FF;
  p = put(s->ln, "aprs start hz=");
  p = dec(p, APRS_HZ, 1);
  p = kv(p, " fsk=", DF_CFG);
  p = kv(p, " sync=", FSK_CTRL);
  p = put(p, " plain");
  say(api, s, p);
  for (;;) {
    uint32_t f, tk, n = 0, over = 0, tried = 0;
    now = api->ms();
    if (now - tkey >= 50) {
      uint16_t r = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      if (r < s->rlo) s->rlo = r;
      if (r > s->rhi) s->rhi = r;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key || now >= RUN_MS) break;
      if (api->level >= 7 && now - s->flag_ms > 120) api->led(0);
    }
#if DF_MODE == 2
    if (!left && now - tsw >= DF_SWAP) {
      tsw += DF_SWAP, mode ^= 1;
      p = kv(s->ln, "s ", (uint32_t)mode);
      p = kv(p, " t=", now);
      p = kv(p, " ok=", s->ok);
      say(api, s, p);
    }
#endif
    f = api->fd_read(0x02) & 0x3002;
    if (f) api->fd_write(0x02, 0);
    tk = api->ticks() & 0xFFFF;
    if (!left) n = 1; /* a look: the empty FIFO gives the same word again */
    else if (mode) {
      acc += (int32_t)(((tk - tlast) & 0xFFFF) * 3); /* a word is 10/3 ms */
      if (acc >= 100) n = udiv((uint32_t)acc, 100), acc -= (int32_t)(100 * n);
      if (n > 8) n = 8; /* the FIFO's size */
    } else if (f & 0x1000) n = DF_THR, tfull = now;
    else if (now - tfull >= DF_LATE) n = DF_THR, tfull = now, tried = 1, late++;
    tlast = tk;
    while (n && !over) {
      /* its low byte was on the air first (the pager's swap), each byte's top bit first */
      uint32_t w = api->fd_read(0x5F), v = (w & 0xFF) << 8 | w >> 8;
      n--;
      if (!left) {
        if (v == prev) break;
        /* the first word of a packet of the chip's. The timed reads stay three words behind */
        left = 1, got = 0, eq = 0, rn = 0, since = 6, tsync = tfull = now, ok0 = s->ok;
        acc = -300;
        s->in = 0, s->sr = 0, s->idle = 0;
        s->dlast = 0, s->dn = DF_IDLE, s->dtone = 1, s->ddur = 0, s->dmd = -1;
      } else if (rn >= 8 && v == s->fifo[rn & 7]) {
        eq++;
        if (tried) { /* no flag and nothing new: the chip has stopped, or will say when it has more */
          if (n == DF_THR - 1) over = 1;
          break;
        }
        if (mode && (!since || since >= 6)) { /* the empty FIFO (a real word may match too, but
                                                 the empty reads come ten apart, or in a row) */
          since = 0;
          break;
        }
      }
      since++;
      s->fifo[rn++ & 7] = (uint16_t)v;
      prev = v, got++;
      s->now_ms = now;
      for (i = 16; i--;) df_sample(api, s, (int32_t)((v >> i) & 1));
      over = s->idle >= FSK_IDLE || s->dn >= DF_QUIET || got >= DF_WORDS;
    }
    if (!left && now - tseek >= FSK_AGAIN) over = 1;
    if (left && over) {
      if (got < DF_SAY && s->ok == ok0) s->fake++;
      else {
        /* a line costs tens of ms of listening: none for a sync word found in noise */
        p = kv(s->ln, "y ", ++s->pre);
        p = kv(p, " t=", tsync);
        p = kv(p, " w=", got);
        p = kv(p, " ok=", s->ok - ok0);
        p = kv(p, " e=", eq);
        p = kv(p, " m=", (uint32_t)mode);
        say(api, s, p);
      }
      left = 0;
    }
    if (!left && now - s->scr_ms >= 1000) {
      /* the minute's line and the screen (20-40 ms), only between packets; the FIFO holds 27 ms,
         so the search is started again after them */
      over = 1;
      s->now_ms = s->scr_ms = now;
      if (now - s->min_ms >= 60000) {
        s->min_ms = now;
        p = kv(s->ln, "m ", udiv(now, 60000));
        p = put(p, " rssi=-");
        p = dec(p, (uint32_t)-KM_RSSI_DBM(s->rlo), 1);
        p = put(p, " pk=-");
        p = dec(p, (uint32_t)-KM_RSSI_DBM(s->rhi), 1);
        p = kv(p, " pre=", s->pre);
        p = kv(p, " ok=", s->ok);
        say(api, s, p);
        s->rlo = 0x1FF, s->rhi = 0;
      }
      if (api->level >= 6) screen(api, s);
    }
    if (over) {
      /* after a line or the screen: what the chip took meanwhile has overrun its FIFO */
      fsk_seek(api, DF_CFG);
      for (i = 0; i < 8; i++) prev = api->fd_read(0x5F); /* what is left in the FIFO */
      prev = (prev & 0xFF) << 8 | prev >> 8;
      tlast = api->ticks() & 0xFFFF, tseek = api->ms();
    }
    api->delay_us(left ? 1000 : 2000);
  }
  s->now_ms = now;
  for (i = 0; i < NSV; i++) api->fd_write(FSK_SAVED[i], s->sv[i]);
  api->fd_write(0x02, 0);
  p = kv(s->ln, "aprs fsk ms=", now);
  p = kv(p, " key=", (uint32_t)key);
  p = kv(p, " pre=", s->pre);
  p = kv(p, " fake=", s->fake);
  p = kv(p, " late=", late);
  say(api, s, p);
  p = put(s->ln, "aprs end ok=");
  p = dec(p, s->ok, 1);
  p = kv(p, " bad=", s->bad);
  p = kv(p, " ts=", s->nts);
  p = kv(p, " v=", (uint32_t)s->votes);
  if (s->votes >= 2) p = hms(put(p, " utc="), utc(s, now), 1);
  p = kv(p, " lost=", s->lost);
  say(api, s, p);
  if (!s->ok && now < RUN_MS) return -2;
  return s->votes;
}
#endif /* FSK_PLAIN */
#endif
