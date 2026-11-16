/* surf_core.inc: band surfing's radio-independent half, shared by the K6's one-chip SURF
   (c/k6/mod/surf.c) and the RT-950 Pro's two-chip SURF (radios/rt950/src/surf2.c).

   SURF (Jeff's idea, 2026-11-08) is a bargraph of signal strength across frequency around a
   listening frequency, on a log "lens" scale, with carets over the peaks it has heard lately and
   side keys that jump between them. The design and every tuning choice are described at the top
   of c/k6/mod/surf.c and in docs/k6-surf.md; Jeff signed the peak behaviour off on 2026-11-12, so
   this file is that logic moved, not changed: the K6 module built from it is byte for byte the
   module built before the move (tests/test_k6_surf.py, and the size and hash checks in
   tests/test_rt950_surf.py).

   What is here: the scale (lens, bins, recentre, window), the sweep of one bin with its quick
   and confirm reads (sweep_bin, conf_now), the floor (floor_q, measure_sfl), the remembered peaks
   and their carets (pm_*), the side-key jumps and their refine, and small text helpers. What is
   not: anything that touches a chip, a keypad, a screen or the audio, and the state machine that
   decides when to sweep and when to listen. That differs by radio. On the K6 one receiver does
   both (listen first, hush on every retune); on the RT-950 Pro one chip sweeps all the time and
   the other listens (docs/rt950-plan.md, "Dual-chip SURF").

   The file that includes this one first includes its stdint types, then may define:
     SF_API          the radio API type that s->api points to (needs say, ms and delay_us members;
                     default k6_api_t)
     SF_SKIP(s, i)   1 for a bin the sweep must not read (the RT-950's guard band round the
                     listener); default 0, so the K6 reads every bin
     SF_EXTRA        more fields at the end of sf_t (the RT-950's second chip)
     SETTLE_US CHECK_US NPM NK ROWS: as below
   and then defines these, which the code here calls:
     static int read_hz(sf_t *s, uint32_t hz, uint32_t us);  the sweep's receiver on hz, us after
                     its tune, in dBm (a look at the keys too, so a long sweep doesn't lose a press)
     static int dbm(sf_t *s);      the sweep's receiver's level now, without a retune
     static void moved(sf_t *s);   the middle (the listening frequency) has moved
     static void demod(sf_t *s);   apply s->md (FM, DSB or AM) to the listening receiver
   SPDX-License-Identifier: MIT */

#ifndef SF_API
#define SF_API k6_api_t
#endif
#ifndef SF_SKIP
#define SF_SKIP(s, i) 0
#endif
#ifndef SF_EXTRA
#define SF_EXTRA
#endif

#define NB 64                   /* bins in the arrays */
#define NV 63                   /* bins on screen: the middle one and 31 each side */
#define C 31                    /* the middle bin, the listening frequency (x = 62-63 on the K6) */
#define KI 8                    /* the lens's edge: bin C+-8 is +-20 kHz from the middle */
#define U_HZ 2500u              /* the unit of channel steps and spans */
#define L_HZ 500u               /* the unit of every bin's offset (the inner steps are finer) */
#ifndef ROWS
#define ROWS 30                 /* bitmap rows (KM_WF_ROWS on the K6) */
#endif
#ifndef SETTLE_US
#define SETTLE_US 3000          /* a sweep read after its tune (Aura's spectrum, wide) */
#endif
#ifndef CHECK_US
#define CHECK_US 12000          /* a settled read */
#endif
#define SUB_HZ 20000u           /* a wide bin: one quick read this far apart across it */
#define SQ_DB 10                /* squelch: settled dB over the settled floor */
#define HYST_DB 3
#define HANG_MS 1500
#define SFL_MS 30000            /* settled floor re-measured this often while sweeping */
#define PEAK_DB 6               /* a peak: quick dB over the floor */
#define DECAY_DB 3
#define MSG_MS 1500
#define WIDE 1
#ifndef NK
#define NK 24                   /* known repeaters (the explorer's table) kept at a time */
#endif
#ifndef NPM
#define NPM 128                 /* remembered peaks (8 B each) */
#endif
#define HMAX 152                /* a caret's height, x 1/8 px: 19 px above the axis's row 25 */
#define PUSH 64                 /* a sweep that hears the peak again lifts its caret 8 px */
#define NCONF 4                 /* settled reads a sweep may spend confirming new peaks */
#define NBAD 6                  /* an unconfirmed peak is read again after this many sweeps hear it */

/* the BK4819 family's audio path (REG_47), the same on the K6's FD6818 and the RT-950's BK4829 */
#define AF_FM 0x6140u
#define AF_AM 0x6740u           /* REG_47 AF 7 (Aura and egzumer's AM set-up, docs/k6-afsweep.md) */
#define AF_DSB 0x6540u          /* REG_47 AF 5, the firmwares' "USB": with REG_3D 0 (zero IF) it is DSB */
#define AF_MUTE 0x6040u

typedef struct {
  uint32_t lo, hi, home;        /* the band's edges, and where BAND and 5 put the middle */
  uint8_t st, si, am;           /* default channel step (STEPS index), span (SPANS index), AM */
  char name[7];
} band_t;                       /* 24 */

/* the channel steps # goes through (Hz); 8.33 kHz is airband's 25/3 */
static const uint32_t STEPS[] = {
    1000u,
    5000u,
    6250u,
    8333u,
    10000u,
    12500u,
    20000u,
    25000u,
    50000u,
    100000u};
#define NSTEPS 10
#define ST_10K 4
#define ST_12K5 5
#define ST_25K 7

/* fixed receiver gains (REG_13: LNA short [9:8], LNA [7:5], mixer [4:3], PGA [2:0]) from the
   most down, about 0, -7, -12, -17, -22, -27, -33 and -45 dB by egzumer's table of the steps;
   -7 is Aura's own top AGC entry */
static const uint16_t GAINS[] = {
    0x03FFu,
    0x03BEu,
    0x037Eu,
    0x035Eu,
    0x033Eu,
    0x031Eu,
    0x031Cu,
    0x031Au};
static const int8_t GAIN_DB[] = {
    0,
    -7,
    -12,
    -17,
    -22,
    -27,
    -33,
    -45};
#define NGAINS 8
/* the span: the last bin's offset from the middle, x 2.5 kHz (100 kHz to 5 MHz) */
static const uint16_t SPANS[] = {
    40,
    100,
    200,
    400,
    1000,
    2000};
#define NSPANS 6
/* the scale's curve for each span (Jeff 2026-11-10: log all the way, +-20 kHz kept in place):
   off(j) = a (r^j - 1) in L_HZ units, each step r times the last, worked in fixed point as
   g += g * k >> 12 from g = 256 a; k and 256 a found offline so that bin C lands exactly on
   the span and bin KI on 20 kHz (20.5 at +-5 MHz) (tests/test_k6_surf.py lens() is the same) */
static const uint16_t LK[] = {
    82,
    344,
    520,
    688,
    906,
    1070};
static const uint16_t LG[] = {
    60201,
    11442,
    6458,
    4196,
    2620,
    1925};
/* the axis's ticks, kHz from the middle, as on log paper; 20 (the lens's edge), 100 and 1000 major */
static const uint16_t TICKS[] = {
    5,
    10,
    15,
    20,
    30,
    40,
    50,
    60,
    70,
    80,
    90,
    100,
    200,
    300,
    400,
    500,
    600,
    700,
    800,
    900,
    1000,
    2000,
    3000,
    4000,
    5000};
/* a caret's fall, ms per 1/8 px (A/B cycles it): the whole height in about 15 s, 1, 5, 20 min */
static const uint16_t FALL_MS[] = {
    100,
    400,
    2000,
    8000};
static const char FALL[4][4] = {
    "15s",
    "1m",
    "5m",
    "20m"};
#define NFALL 4
#define MD_FM 0
#define MD_DSB 1
#define MD_AM 2
#define NMD 3
static const char MD_NAME[NMD][4] = {
    "FM",
    "DSB",
    "AM"};
/* the chip's squelch: glitch close (REG_4D [7:0]), glitch open (REG_4E [7:0]), noise close/open
   (REG_4F), RSSI open/close (REG_78); open all the way = egzumer's squelch 0: glitch 255, noise
   127, RSSI 0 */
static const uint8_t SQ_REGS[4] = {
    0x4D,
    0x4E,
    0x4F,
    0x78};

/* a remembered peak: where (the loudest read's frequency), how wide the bin that heard it was
   (a coarse outer bin only says roughly where), its caret's height (0: free) and whether a
   settled read confirmed it (a quick-read noise bump stays BAD, unhoppable, until it falls) */
typedef struct {
  uint32_t f;
  uint16_t w;                   /* x L_HZ */
  uint8_t h, bad;
} pm_t;

typedef struct {
  const SF_API *api;
  const band_t *bd;             /* the band */
  uint8_t *bmp;
  uint32_t ch, cen, now;         /* channel step, the middle (listening) frequency */
  uint32_t t_flush, t_open, t_full, t_sfl, t_msg, t_key, t_rep, t_auto, t_chk;
  int next, sweeps, npm;
  int sfl, sel_dbm;
  uint8_t si, qfl, parked, tuned, check, mon, hold, audio, hush, key, raw, full, refine, stale;
  uint8_t st, gx;               /* the channel step (STEPS index); fixed gain GAINS[gx - 1], 0 auto */
  uint8_t qk, k1;               /* a key caught during a long sweep or measure (kp_peek), the last peek */
  uint8_t sc;                   /* surfkeys: the script's next key */
  uint8_t fall;                 /* FALL_MS index */
  uint8_t md, zif;              /* key 4: the demodulator (MD_FM, MD_DSB, MD_AM); REG_3D written */
  uint32_t t_fall;              /* the carets' fall: worked up to here */
  uint32_t rw;                  /* a jump's refine: how far (Hz) from the middle it may look */
  pm_t *pm;                     /* the remembered peaks (npm of them; none with no room) */
  uint16_t af;                  /* REG_47 with the audio open: FM or AM */
  uint16_t off[C + 1];          /* bin C+-k's offset from the middle, x L_HZ */
  uint32_t hz[NB];              /* each bin's frequency, 0 outside the band */
  uint32_t pk[NB];              /* where in its range a bin's loudest read was */
  uint32_t r47, odr, r13, r73, r7e, r3d;
  uint16_t sq[4];               /* monitor: the chip's squelch (SQ_REGS) as Aura had it */
  uint8_t lvl[NB];              /* bars: quick reads, dBm + 174, 0 unknown */
  uint32_t heard[2];            /* bins whose last read held their bar up (not a bar falling) */
  uint8_t cf[NB];               /* this sweep: a bin's settled read right after its quick read rose
                                   to a peak, dBm + 174 (0 none) */
  uint8_t ncf;                  /* such reads left this sweep */
  char ln[40];
  char msg[22];                 /* line 5 for MSG_MS after a key: "no peak", "gain -12 dB" */
  uint32_t khz[NK];             /* the repeater table's valid entries: rx_hz, and their slots */
  uint16_t kslot[NK];
  int nk;
  /* (work space: the state lives in the module store's free RAM, not on the stack) */
  uint32_t old[NV];             /* recentre: the old scale's loudest reads */
  uint8_t lv[NV];               /* recentre: its levels */
  uint8_t tmp[NB];              /* floor_q's sort, draw's carets */
  uint8_t bm[16 * ROWS];        /* the bitmap (KM_WF_ROWS rows) */
  SF_EXTRA
} sf_t;

/* what the radio provides (see the top of this file) */
static int read_hz(sf_t *s, uint32_t hz, uint32_t us);
static int dbm(sf_t *s);
static void moved(sf_t *s);
static void demod(sf_t *s);

static uint32_t udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

/* v in decimal, a '.' before its last dot digits */
static char *fix(char *p, uint32_t v, int dot) {
  char t[11];
  int n = 0;
  do {
    uint32_t q = udiv(v, 10);
    t[n++] = (char)('0' + v - 10 * q);
    v = q;
  } while (v || n <= dot);
  while (n) {
    if (dot && n == dot) *p++ = '.';
    *p++ = t[--n];
  }
  return p;
}

static char *sint(char *p, int v) {
  if (v < 0) *p++ = '-', v = -v;
  return fix(p, (uint32_t)v, 0);
}

static char *mhz(char *p, uint32_t hz, int dec) { return fix(p, udiv(hz, dec == 5 ? 10 : dec == 4 ? 100 : 1000), dec); }

/* the listening frequency's decimals: 4, or 5 off the 100 Hz raster (6.25 and 8.33 kHz steps) */
static int cdec(sf_t *s) { return s->cen - 100 * udiv(s->cen, 100) ? 5 : 4; }

/* a step in kHz: "6.25k", "12.5k", "25k" */
static char *stepn(char *p, uint32_t step) {
  p = fix(p, udiv(step, 10), 2);
  while (p[-1] == '0') p--;
  if (p[-1] == '.') p--;
  *p++ = 'k';
  return p;
}

/* a span: "100k", "2.5M" */
static char *spanx(char *p, uint32_t hz) {
  if (hz < 1000000u) return stepn(p, hz);
  p = fix(p, udiv(hz, 100000), 1);
  if (p[-1] == '0') p -= 2;
  *p++ = 'M';
  return p;
}

static void say_end(sf_t *s, char *p) {
  *p = 0;
  s->api->say(s->ln);
}

static int read_at(sf_t *s, int i, uint32_t us) { return read_hz(s, s->hz[i], us); }

static void note(sf_t *s, const char *t) {
  put(s->msg, t)[0] = 0;
  s->t_msg = s->now + MSG_MS;
}

/* ---- the scale ---- */

/* the offsets for the span: a smooth geometric curve out from the middle (LK, LG), each step at
   least one unit */
static void lens(sf_t *s) {
  uint32_t g0 = LG[s->si], g = g0, k = LK[s->si], j, v;
  s->off[0] = 0;
  for (j = 1; j <= C; j++) {
    g += (g * k) >> 12;
    v = (g - g0 + 128) >> 8;
    s->off[j] = (uint16_t)(v > s->off[j - 1] ? v : s->off[j - 1] + 1u);
  }
}

/* bin i's distance from the middle, in bins */
static int dist(int i) { return i < C ? C - i : i - C; }

/* the step around bin i, x L_HZ */
static uint32_t spu(sf_t *s, int i) {
  int a = dist(i);
  return a < C ? s->off[a + 1] - s->off[a] : s->off[C] - s->off[C - 1];
}

/* the step around bin i (Hz) */
static uint32_t sp(sf_t *s, int i) { return L_HZ * spu(s, i); }

/* the frequencies bin i stands for: halfway to its neighbours */
static void cov(sf_t *s, int i, uint32_t *lo, uint32_t *hi) {
  int a = dist(i);
  uint32_t out = sp(s, i) / 2, in = a ? L_HZ * (s->off[a] - s->off[a - 1]) / 2 : out;
  *lo = s->hz[i] - (i < C ? out : in);
  *hi = s->hz[i] + (i < C ? in : out);
}

/* the bin nearest f, if f is within half its step of it; -1 if none */
static int bin_of(sf_t *s, uint32_t f) {
  int i, b = -1;
  uint32_t d, bd = ~0u;
  for (i = 0; i < NV; i++)
    if (s->hz[i]) {
      d = s->hz[i] > f ? s->hz[i] - f : f - s->hz[i];
      if (d < bd) bd = d, b = i;
    }
  return b >= 0 && 2 * bd <= sp(s, b) ? b : -1;
}

/* settled floor: the second lowest of 8 bins' settled reads */
static void measure_sfl(sf_t *s) {
  int j, a = 999, b = 999;
  for (j = 3; j < NV; j += 8) {
    int d;
    if (!s->hz[j] || SF_SKIP(s, j)) continue;
    d = read_at(s, j, CHECK_US);
    if (d < a) b = a, a = d;
    else if (d < b) b = d;
  }
  if (a == 999) a = read_at(s, C, CHECK_US);   /* (a band edge: the middle is always in it) */
  s->sfl = b < 999 ? b : a;
  s->t_sfl = s->api->ms();
  s->tuned = 0;
}

/* the middle on hz (in the band, on its channel raster) and the scale around it; each bin keeps
   the loudest old bin whose loudest read falls in its range */
static void recentre(sf_t *s, uint32_t hz) {
  const band_t *b = s->bd;
  uint32_t *old = s->old, lo, hi;
  uint8_t *lv = s->lv;
  int i, j;
  if (hz < b->lo) hz = b->lo;
  if (hz > b->hi) hz = b->hi;
  s->cen = b->lo + udiv(hz - b->lo + s->ch / 2, s->ch) * s->ch;
  if (s->cen > b->hi) s->cen -= s->ch;
  for (i = 0; i < NV; i++) {
    uint32_t o = L_HZ * s->off[dist(i)], f = i < C ? s->cen - o : s->cen + o;
    s->cf[i] = 0;                       /* (the old scale's reads are not these bins') */
    old[i] = s->pk[i];
    lv[i] = s->lvl[i];
    s->hz[i] = s->pk[i] = f >= b->lo && f <= b->hi ? f : 0;
    s->lvl[i] = 0;
  }
  s->stale = 1;
  for (i = 0; i < NV; i++) {
    if (!s->hz[i]) continue;
    cov(s, i, &lo, &hi);
    for (j = 0; j < NV; j++)
      if (lv[j] > s->lvl[i] && old[j] >= lo && old[j] < hi) s->lvl[i] = lv[j], s->pk[i] = old[j];
  }
}

/* a fresh scale on hz at the span SPANS[si] */
static void window(sf_t *s, uint32_t hz) {
  int i;
  s->ch = STEPS[s->st];
  lens(s);
  for (i = 0; i < NB; i++) s->lvl[i] = 0;
  recentre(s, hz);
  s->t_chk = s->api->ms();
  s->next = 0;
  s->full = 0;
  s->refine = 0;
  measure_sfl(s);
  s->t_sfl = s->t_sfl - SFL_MS;        /* and again after the first sweep: reads right after a
                                         new band's big retune may not have settled (Jeff,
                                         2026-11-12: no carets after BAND until UP / DOWN) */
  moved(s);
}

static int is_peak(sf_t *s, int i) {
  int v = s->lvl[i];
  if (v < s->qfl + PEAK_DB) return 0;      /* (an unknown bin, 0, never is) */
  if (i > 0 && s->lvl[i - 1] > v) return 0;
  if (i + 1 < NV && s->lvl[i + 1] >= v) return 0;
  return 1;
}

/* "sel <bar MHz> <its settled dBm> <the settled floor>" */
static void say_sel(sf_t *s) {
  char *p = put(s->ln, "sel ");
  p = mhz(p, s->cen, cdec(s));
  *p++ = ' ';
  p = sint(p, s->sel_dbm);
  *p++ = ' ';
  say_end(s, sint(p, s->sfl));
}

/* ---- remembered peaks (Jeff, 2026-11-11): a caret over each peak heard lately ---- */

/* f on the band's channel raster */
static uint32_t raster(sf_t *s, uint32_t f) {
  uint32_t lo = s->bd->lo;
  return lo + udiv(f - lo + s->ch / 2, s->ch) * s->ch;
}

/* after a sweep: each peak lifts the caret of the remembered peak it is (the nearest within half
   the wider bin of the two, or a channel), or takes a free entry (else the lowest caret's) once
   a settled read confirms it (SQ_DB over the settled floor; bench run 1 on 2026-11-08 stopped
   on -103 to -122 dBm quick-read bumps without it), NCONF a sweep at most; a peak that fails
   is kept BAD so it isn't read again until its caret falls */
static void pm_push(sf_t *s) {
  int i, k, c, conf = NCONF;
  for (i = 0; i < NV; i++) {
    uint32_t f, w, t, d, bd = ~0u;
    pm_t *e, *b = 0, *lo = s->pm;
    int a = i, z = i;
    if (!is_peak(s, i) || !(s->heard[i >> 5] & (1u << (i & 31)))) continue;   /* (heard now) */
    /* a carrier fills the lens's bins inside the wide filter (+-12 kHz): its flat top's middle */
    while (a > C - KI && z <= C + KI && s->lvl[a - 1] + 2 >= s->lvl[i]) a--;
    while (z < C + KI && a >= C - KI && s->lvl[z + 1] + 2 >= s->lvl[i]) z++;
    f = (s->pk[a] + s->pk[z]) >> 1;
    for (c = 0, k = a; k <= z; k++)
      if (s->cf[k] > c) c = s->cf[k];
    /* how well it knows where: a bin's width, but a wide bin's loudest read is one of reads
       SUB_HZ apart across it, so never coarser than that (matching on a wide bin's whole width,
       hundreds of kHz at +-2.5 MHz, gave 463.950 to the caret of 463.6375 next to it: the side
       keys passed it, Jeff 2026-11-11) */
    w = spu(s, i);
    if (w > SUB_HZ / L_HZ) w = SUB_HZ / L_HZ;
    for (k = 0; k < s->npm; k++) {
      e = &s->pm[k];
      if (e->h < lo->h) lo = e;
      if (!e->h) continue;
      d = e->f > f ? e->f - f : f - e->f;
      t = L_HZ * (e->w > w ? e->w : w);
      if (t < s->ch) t = s->ch;
      if (2 * d <= t && d < bd) bd = d, b = e;
    }
    if (b) {
      if (!b->bad) {
        if (w <= b->w) b->f = f, b->w = (uint16_t)w;   /* a finer bin says better where */
        b->h = (uint8_t)(b->h + PUSH > HMAX ? HMAX : b->h + PUSH);
        continue;
      }
      /* unconfirmed: a bump, or a station that had just unkeyed at its read (Jeff, 2026-11-11:
         a -75 dBm one got no caret); read again once NBAD sweeps have heard it */
      if (c - 174 >= s->sfl + SQ_DB) {  /* the sweep's own settled read caught it on */
        b->bad = 0;
        if (w <= b->w) b->f = f, b->w = (uint16_t)w;
        continue;
      }
      if (--b->bad) continue;
      b->bad = 1;                      /* (due: if no read is left this sweep, the next one) */
      lo = b;
    }
    if (!s->npm) continue;
    if (c) {
      d = (uint32_t)(c - 174);          /* read in the sweep, while it was on */
    } else {
      if (!conf || s->qk) continue;
      conf--;
      /* (at its loudest read, not its channel: a wide bin's reads are SUB_HZ apart, and its
         channel can be 12.5 kHz from the station, off the wide filter's edge) */
      d = (uint32_t)read_hz(s, f, CHECK_US);
      s->tuned = 0;
    }
    lo->f = f;
    lo->w = (uint16_t)w;
    lo->h = PUSH;
    lo->bad = (int)d < s->sfl + SQ_DB ? NBAD : 0;
  }
}

/* the bar's channel read settled SQ_DB over the floor (the squelch opened on it, or a parked
   read): that is a confirm. Its caret (or a new one) is good and at least PUSH high. A single
   confirm read after a sweep can land between overs and leave a station that plays on the bar
   with no caret (Jeff, 2026-11-11: 464.500 sounded fine and its bar stood tall) */
static void pm_heard(sf_t *s) {
  pm_t *e, *b = 0, *lo = s->pm;
  uint32_t d, t, bd = ~0u, f = s->cen;
  int k;
  if (!s->npm) return;
  for (k = 0; k < s->npm; k++) {
    e = &s->pm[k];
    if (e->h < lo->h) lo = e;
    if (!e->h) continue;
    d = e->f > f ? e->f - f : f - e->f;
    t = L_HZ * e->w;
    if (t < s->ch) t = s->ch;
    if (2 * d <= t && d < bd) bd = d, b = e;
  }
  if (!b) {
    b = lo;
    b->f = f;
    b->w = (uint16_t)udiv(s->ch, L_HZ);
    b->h = 0;
  }
  b->bad = 0;
  if (b->h < PUSH) b->h = PUSH;
}

/* 1 if a confirmed caret already covers f (no settled read needed for it) */
static int pm_good(sf_t *s, uint32_t f) {
  pm_t *e;
  uint32_t d, t;
  int k;
  for (k = 0; k < s->npm; k++) {
    e = &s->pm[k];
    if (!e->h || e->bad) continue;
    d = e->f > f ? e->f - f : f - e->f;
    t = L_HZ * e->w;
    if (t < s->ch) t = s->ch;
    if (2 * d <= t) return 1;
  }
  return 0;
}

/* the carets fall one 1/8 px every FALL_MS (time, not sweeps: a sweep takes 0.1 to 2.5 s); not
   while the scan is paused (hold on a station that plays, or monitor: no sweeps), and on from
   where they were after it (Jeff, 2026-11-13) */
static void pm_fall(sf_t *s) {
  uint32_t n = udiv(s->now - s->t_fall, FALL_MS[s->fall]);
  int k;
  if (s->parked && (s->hold || s->mon)) n = 0, s->t_fall = s->now;
  if (!n) return;
  s->t_fall += n * FALL_MS[s->fall];
  if (n > 255) n = 255;
  for (k = 0; k < s->npm; k++) s->pm[k].h = (uint8_t)(s->pm[k].h > n ? s->pm[k].h - n : 0);
}

/* a side key: the middle to the nearest remembered peak (caret still up, confirmed, on another
   channel) going d, whether or not it is on the air now: no search, the sweeps go on as they
   were; a caret only a coarse bin heard is refined after the next read of the lens (refine_lens). None that way: "no peak" and the middle stays (the bench build wraps) */
static void jump(sf_t *s, int d) {
  pm_t *e, *b = 0;
  uint32_t c, bc = 0;
  int k;
  for (k = 0; k < s->npm; k++) {
    e = &s->pm[k];
    if (!e->h || e->bad || e->f < s->bd->lo || e->f > s->bd->hi) continue;
    c = raster(s, e->f);
    if (d > 0 ? c > s->cen && (!b || c < bc) : c < s->cen && (!b || c > bc)) b = e, bc = c;
  }
  if (!b) {
    note(s, "no peak");
#ifdef SURF_AUTO_S
    window(s, s->bd->lo);
#endif
    return;
  }
  recentre(s, b->f);
  b->f = s->cen;                        /* (it is that channel now: its caret over the middle) */
  /* a caret only a coarse bin heard says roughly where: refine within that bin's width. One a
     fine bin heard is its channel: no refine (it pulled a jump to the weaker of two stations
     20 kHz apart straight back to the louder: Jeff, 2026-11-11) */
  s->refine = L_HZ * b->w > s->ch;
  s->rw = L_HZ * b->w / 2 > s->ch ? L_HZ * b->w / 2 : s->ch;   /* (the refine's reach) */
  if (s->refine) b->w = (uint16_t)udiv(s->ch, L_HZ);
  moved(s);
#ifdef SURF_AUTO_S
  s->sel_dbm = read_at(s, C, CHECK_US);   /* the bench: its level as the jump lands */
  s->tuned = 0;
  say_sel(s);
#endif
}

/* the middle to f (in the band) and listen there */
static void go_to(sf_t *s, uint32_t f) {
  if (f < s->bd->lo || f > s->bd->hi) return;
  recentre(s, f);
  moved(s);
}

/* a new band: its default step and span, AM or FM, the middle on its centre */
static void band(sf_t *s, const band_t *b) {
  int k;
  for (k = 0; k < s->npm; k++) s->pm[k].h = 0;   /* (the remembered peaks are the band's) */
  s->bd = b;
  s->st = b->st;
  s->si = b->si;
  s->md = b->am ? MD_AM : MD_FM;
  demod(s);
  window(s, b->home);
}

/* ---- sweeping ---- */

/* a quick read r (dBm) at f, where the bin had o, rose to a new peak with no confirmed caret: a
   settled read is due here and now (one of NCONF a sweep): wait for it, 1. One after the sweep
   (0.1-2.5 s later) missed a station that pulses, and its caret never came (Jeff, 2026-11-12:
   a peak at +20 kHz). After a move a bin's level is lent from the old scale: any rise counts */
static int conf_now(sf_t *s, int r, int o, uint32_t f) {
  r += 174;
  if (!s->full || !s->ncf || r < s->qfl + PEAK_DB || (r + DECAY_DB <= o && !s->stale) || pm_good(s, f))
    return 0;
  s->ncf--;
  s->api->delay_us(CHECK_US - SETTLE_US);
  return 1;
}

/* bin i's quick level: the loudest of reads SUB_HZ apart across its range (the wide filter's
   width: a coarse bin far out would miss a carrier between two bins), and where it was */
static void sweep_bin(sf_t *s, int i) {
  uint32_t lo, hi, f, at = s->hz[i];
  int r, o = s->lvl[i], v;
  cov(s, i, &lo, &hi);
  s->cf[i] = 0;
  if (hi - lo <= SUB_HZ) {                 /* the lens and bins up to 20 kHz wide: one read */
    r = read_at(s, i, SETTLE_US);
    /* a new peak's settled read, here and now: one after the sweep (0.1-2.5 s later) misses a
       station that pulses, and its caret never came (Jeff, 2026-11-12: a peak at +20 kHz) */
    if (conf_now(s, r, o, at)) {
      v = dbm(s) + 174;
      s->cf[i] = (uint8_t)(v < 1 ? 1 : v);
    }
  } else {
    for (r = -999, f = lo + SUB_HZ / 2; f < hi && !s->qk; f += SUB_HZ)
      if (f >= s->bd->lo && f <= s->bd->hi && (v = read_hz(s, f, SETTLE_US)) > r) {
        r = v, at = f;
        /* the same for a wide bin: at its first sub-read that rises to a peak, once a bin */
        if (!s->cf[i] && conf_now(s, r, o, f)) {
          v = dbm(s) + 174;
          s->cf[i] = (uint8_t)(v < 1 ? 1 : v);
        }
      }
    if (s->qk) return;                     /* a key came: it goes first (the bin keeps its bar) */
    if (r == -999) r = read_at(s, i, SETTLE_US);
  }
  if (r + 174 >= o || !o || s->stale) s->pk[i] = at;
  r += 174;
  if (r < 1) r = 1;
  if (r + DECAY_DB > o) s->heard[i >> 5] |= 1u << (i & 31);
  else s->heard[i >> 5] &= ~(1u << (i & 31));
  /* (the first sweep after a move replaces the levels the old scale lent it) */
  s->lvl[i] = (uint8_t)(r >= o || !o || s->stale ? r : (o - r > DECAY_DB ? o - DECAY_DB : r));
}

/* the quartile of the scale's known bars */
static void floor_q(sf_t *s) {
  uint8_t *t = s->tmp;
  int n = 0, i, j, k;
  for (i = 0; i < NV; i++)
    if (s->lvl[i]) t[n++] = s->lvl[i];
  if (!n) return;
  k = n >> 2;
  for (i = 0; i <= k; i++)
    for (j = i + 1; j < n; j++)
      if (t[j] < t[i]) {
        uint8_t x = t[i];
        t[i] = t[j], t[j] = x;
      }
  s->qfl = t[k];
}

/* after a jump to a caret a coarse bin heard: the loudest bin of the lens within half that
   bin's width (or a channel), if that is another channel, is the station: the middle moves
   there once more; 1 if it moved */
static int refine_lens(sf_t *s) {
  int i, b = C, a, z;
  uint32_t f;
  s->refine = 0;
  for (i = C - KI; i <= C + KI; i++)
    if (s->hz[i] && (uint32_t)L_HZ * s->off[dist(i)] <= s->rw && s->lvl[i] > s->lvl[b]) b = i;
  if (b == C || s->lvl[b] <= s->lvl[C] + 1) return 0;
  /* a carrier fills the lens's bins inside the wide filter: its flat top's middle */
  for (a = b; a > C - KI && s->lvl[a - 1] + 2 >= s->lvl[b]; a--) {}
  for (z = b; z < C + KI && s->lvl[z + 1] + 2 >= s->lvl[b]; z++) {}
  f = (s->pk[a] + s->pk[z]) >> 1;
  if (raster(s, f) == s->cen) return 0;
  recentre(s, f);
  moved(s);
  return 1;
}

static void sweep_done(sf_t *s) {
  s->sweeps++;
  s->full = 1;
  s->stale = 0;
  floor_q(s);
  pm_push(s);
  s->ncf = NCONF;
  if (s->refine) refine_lens(s);
}
