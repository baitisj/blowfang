/* surf: band surfing on the K6 (Jeff's idea, 2026-11-08). Receive only.

   The screen is a bargraph of signal strength (Y) across frequency (X): 63 bins, 2 pixels each,
   drawn as the level-6 waterfall bitmap (30 rows of 128 pixels, y = 21-50). The listening
   frequency is always the middle bin (x = 62-63), its bar drawn like the others with a caret
   above it (it blinked until 2026-11-10; Jeff: no purpose), and the scale moves with it. The
   scale is logarithmic out from the middle (Jeff, 2026-11-09 and -10): each bin's step is a
   fixed ratio larger than the one inside it, the 8th bin each side always lands on +-20 kHz
   (the "lens", a quarter of the screen), and the 31st on the span (1 / 3: +-100 kHz to
   +-5 MHz); at +-1 MHz the steps run from 1.5 kHz in the middle to 145 kHz at the edge. Every
   bin reads with the wide filter (the narrow one, 2026-11-09, gave the lens a flat top and a
   lower floor than the rest; Jeff 2026-11-10: wide again). Under the bars an axis line has
   ticks as on log paper, every 5 kHz to 20 kHz, every 10 kHz to 100 kHz, every 100 kHz to 1 MHz
   and every 1 MHz, taller at 20 kHz, 100 kHz and 1 MHz (Jeff: "axis tic marks to help understand
   the frequency intervals"); line 5 labels it "-1M   -20k +20k   +1M" (the span at each end, the
   lens's edges under their ticks). A small tick over a bar marks a peak, a dot in the top row a repeater
   the explorer saved (its table, c/k6/rt.h, read once at the start). Bins outside the band stay
   empty. The title line says the band and its channel step (a fixed gain in place of SURF), line 1 the listening frequency and
   its level in dBm (a settled read), line 5 the axis's labels, or with the bar on a saved
   repeater its call, offset and TX tone ("N0CALL +5.0 103.5").

   Keys (the module scans the keypad itself: the module API has key_down but no key identity;
   the matrix is Aura's src/device/keypad.rs at edf3b98, rows PB0/PB4/PB10/PB11/PB9, columns
   PC13/PC14/PF6/PB14, side keys read with no row driven):
     UP / DOWN       the listening frequency one channel up / down (held: repeats); the scale
                     moves with it, each bin keeping the level of the old bin nearest it
     SIDE1 / SIDE2   (the orange side keys) jump to the next peak above / below that a settled
                     read confirms as a signal (SQ_DB over the settled floor), on the channel
                     raster; after the next sweep the loudest bin of the lens, if it is another
                     channel, moves it once more (an outer bin is coarse). With none left on
                     screen, the scale's edge becomes the middle and it looks again after the
                     next sweep, on up the band; at the band's edge with none found, line 5 says
                     "no peak" and the middle goes back where the search began (Jeff, 2026-11-10)
     BAND            next band: 2m, 1.25m, 70cm, GMRS, marine + NOAA WX, airband (AM, 118-137
                     MHz, receive only); each starts at its centre with its default step and span
     #               next channel step: 5, 6.25, 8.33, 10, 12.5, 20, 25, 50, 100 kHz (the middle
                     stays; UP / DOWN then move onto the new raster; BAND resets the step)
     5               the band's centre
     7 / 9           fixed receiver gain down / up: 0, -7, -12, -17, -22, -27, -33, -45 dB (from
                     auto, 9 starts at the top and 7 at -7); the title shows it ("G-12 70cm 25k")
     8               the receiver's own AGC again (the title says SURF)
     1 / 3           zoom in / out: the span +-100 kHz, 250 kHz, 500 kHz, 1 MHz, 2.5 MHz, 5 MHz
     *               monitor: audio open on the bar all the time, no sweeping
     0               hold: no refresh sweeps while a signal plays (default: one every 5 s)
     MENU            say the bar's frequency and level on the serial port
     EXIT            leave (the module waits for the key's release, so the pager doesn't see it)

   One receiver both sweeps and listens, so the module listens first: while the bar's channel
   is quiet it sweeps (audio muted), and every CHK_MS it goes back to the bar for a settled
   read; a signal there SQ_DB over the settled floor opens the audio (REG_47 FM, speaker amp on
   PB2, as dmrdet) and parks on it. It closes HANG_MS after the signal drops, and then sweeps
   again. While parked, a refresh sweep every REFRESH_MS (about 0.3 s of muted audio) keeps the
   bars current, unless hold (0) or monitor is on. Any retune with the audio open turns the
   speaker amp off as well until the bar has settled again (hush): REG_47 muted alone buzzed.

   Levels: sweep reads are quick (SETTLE_US after the tune, as Aura's own spectrum: about 59 %
   of the way in dB from the -173 dBm restart, Phase 0), so the bars are relative: the floor is
   the quartile of the window's bins, each pixel of height about 1.1 quick dB. Bars fall at
   most DECAY_DB a sweep and rise at once. The squelch and line 1 use settled reads (CHECK_US):
   the settled floor is the second lowest of 8 bins' settled reads, measured when the window
   changes and every 30 s of sweeping, and a quieter read of the bar lowers it. The receiver's
   AGC is left alone (dmrdet's frozen gain shifts RSSI by up to 15 dB).

   surfauto.c builds the same module for unattended bench runs: SIDE1 presses itself every
   SURF_AUTO_MS (each peak it finds says "sel" with the settled read that confirmed it and the
   settled floor), it wraps to the band's bottom at
   the top, and it leaves after SURF_AUTO_S; keys still work. Before it ends it says its last
   bargraph ("bar 441.2000 1000k lens": the middle and the span, then two lines of a letter a
   bin, tools/k6_screen.py draws them).

   Lines (K6 MOD say ...): "surf 70cm 440.850 25k", "sel 441.2000 -71 -127" (MENU, or the
   bench build's jumps: the bar, its settled dBm, the settled floor), "end sw=N".
   Writes REG_47 and GPIOB bit 2 (speaker), REG_14, REG_49 and REG_73 bit 4 (AM's set-up on
   airband), REG_13 and REG_7E (fixed gain) and the keypad rows' GPIOB BSRR/BRR bits, all put back.
   Reads the SPI flash (level 5's fl_read), never writes it. No TX. The bitmap (480 B), the state
   (~750 B) and a move's copy of the old scale (~320 B) live on the stack, about 1.6 KB of the
   3 KB a module has at its call; the store holds only the code.
   Returns the sweeps done, or -3 (an image below level 6).
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 6
#define K6_MODULE_OPT z
#endif
#include "../k6_mod.h"

#define NB 64                   /* bins in the arrays */
#define NV 63                   /* bins on screen: the middle one and 31 each side */
#define C 31                    /* the middle bin, the listening frequency (x = 62-63) */
#define KI 8                    /* the lens's edge: bin C+-8 is +-20 kHz from the middle */
#define U_HZ 2500u              /* the unit of channel steps and spans */
#define L_HZ 500u               /* the unit of every bin's offset (the inner steps are finer) */
#define ROWS 30                 /* bitmap rows (KM_WF_ROWS) */
#define BAR0 6                  /* first bar row: caret rows 1-3, peak ticks row 4 */
#define AX 27                   /* the axis: its line, then ticks in rows 28 (all) and 29 (major) */
#define BARH (AX - BAR0)
#define SETTLE_US 3000          /* a sweep read after its tune (Aura's spectrum, wide) */
#define CHECK_US 12000          /* a settled read */
#define CHK_MS 70               /* sweeping: a check of the bar's channel this often */
#define SUB_HZ 20000u           /* a wide bin: one quick read this far apart across it */
#define SQ_DB 10                /* squelch: settled dB over the settled floor */
#define HYST_DB 3
#define HANG_MS 1500
#define PARK_US 20000           /* a parked read every ~20 ms */
#define REFRESH_MS 5000
#define SFL_MS 30000            /* settled floor re-measured this often while sweeping */
#define FLUSH_MS 200            /* the screen (each flush ~20-40 ms) */
#define PEAK_DB 6               /* a peak: quick dB over the floor */
#define DECAY_DB 3
#define MSG_MS 1500
#define WIDE 1
#define NK 24                   /* known repeaters (the explorer's table) kept at a time */
#define RT_BASE 0x0F0000u       /* c/k6/rt.h: 1024 slots of 64 B */
#define RT_SLOTS 1024

#define GPIOB 0x48000400u
#define GPIOC 0x48000800u
#define GPIOF 0x48001400u
#define IDR 0x10u
#define ODR 0x14u
#define BSRR 0x18u
#define BRR 0x28u
#define SPK (1u << 2)
#define ROWS_B ((1u << 0) | (1u << 4) | (1u << 9) | (1u << 10) | (1u << 11))
#define AF_FM 0x6140u
#define AF_AM 0x6740u           /* REG_47 AF 7 (Aura and egzumer's AM set-up, docs/k6-afsweep.md) */
#define AF_MUTE 0x6040u
#define AM_49 ((50u << 7) | 32u)

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

typedef struct {
  uint32_t lo, hi;
  uint8_t st, si, am;           /* default channel step (STEPS index), span (SPANS index), AM */
  char name[7];
} band_t;                       /* 20 */

/* the channel steps # goes through (Hz); 8.33 kHz is airband's 25/3 */
static const uint32_t STEPS[] = {
    5000u,
    6250u,
    8333u,
    10000u,
    12500u,
    20000u,
    25000u,
    50000u,
    100000u};
#define NSTEPS 9
#define ST_10K 3
#define ST_12K5 4
#define ST_25K 6

/* airband is receive only (the K6 never transmits there) and sits below its 136-174 MHz filter */
static const band_t BANDS[] = {
    {144000000u, 148000000u, ST_10K, 2, 0, "2m"},
    {222000000u, 225000000u, ST_10K, 2, 0, "1.25m"},
    {430000000u, 450000000u, ST_25K, 3, 0, "70cm"},
    {462550000u, 467725000u, ST_12K5, 4, 0, "GMRS"},
    {156000000u, 162550000u, ST_25K, 3, 0, "MAR/WX"},
    {118000000u, 137000000u, ST_25K, 3, 1, "AIR"}};
#define NBANDS 6
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
static const uint16_t SPANS[] = {40, 100, 200, 400, 1000, 2000};
#define NSPANS 6
/* the scale's curve for each span (Jeff 2026-11-10: log all the way, +-20 kHz kept in place):
   off(j) = a (r^j - 1) in L_HZ units, each step r times the last, worked in fixed point as
   g += g * k >> 12 from g = 256 a; k and 256 a found offline so that bin C lands exactly on
   the span and bin KI on 20 kHz (20.5 at +-5 MHz) (tests/test_k6_surf.py lens() is the same) */
static const uint16_t LK[] = {82, 344, 520, 688, 906, 1070};
static const uint16_t LG[] = {60201, 11442, 6458, 4196, 2620, 1925};
/* the axis's ticks, kHz from the middle, as on log paper; 20 (the lens's edge), 100 and 1000 major */
static const uint16_t TICKS[] = {5,   10,  15,  20,  30,  40,  50,  60,  70,   80,   90,   100,  200,
                                 300, 400, 500, 600, 700, 800, 900, 1000, 2000, 3000, 4000, 5000};

/* Aura's KEY_TABLE: phase 0 (no row driven) side keys, then rows PB0, PB4, PB10, PB11, PB9;
   A/B = side 1/2, U/D up/down, M menu, X exit, N band, V vm, W a/b */
static const char KEYS[24] = {0,   'A', 0,   'B', '7', '8', '#', '9', '4', '5', '0', '6',
                              '1', '2', '*', '3', 'M', 'U', 'X', 'D', 'V', 'W', 0,   'N'};
static const uint8_t ROWPIN[6] = {0xFF, 0, 4, 10, 11, 9};

typedef struct {
  const k6_api_t *api;
  const band_t *bd;             /* the band */
  uint8_t *bmp;
  uint32_t ch, cen, now;         /* channel step, the middle (listening) frequency */
  uint32_t t_flush, t_open, t_full, t_sfl, t_msg, t_key, t_rep, t_auto, t_chk;
  int next, pend, sj, sweeps;
  int sfl, sel_dbm;
  uint8_t si, qfl, parked, tuned, check, mon, hold, audio, hush, key, raw, full, refine, stale;
  uint8_t st, gx;               /* the channel step (STEPS index); fixed gain GAINS[gx - 1], 0 auto */
  uint32_t home;                /* where a side key's search began: back there if it finds none */
  uint16_t af;                  /* REG_47 with the audio open: FM or AM */
  uint16_t off[C + 1];          /* bin C+-k's offset from the middle, x L_HZ */
  uint32_t hz[NB];              /* each bin's frequency, 0 outside the band */
  uint32_t pk[NB];              /* where in its range a bin's loudest read was */
  uint32_t r47, odr, r13, r14, r49, r73, r7e;
  uint8_t lvl[NB];              /* bars: quick reads, dBm + 174, 0 unknown */
  char ln[40];
  char msg[22];                 /* line 5 for MSG_MS after a key: "no peak", "gain -12 dB" */
  uint32_t khz[NK];             /* the repeater table's valid entries: rx_hz, and their slots */
  uint16_t kslot[NK];
  int nk;
} sf_t;

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

static char *mhz(char *p, uint32_t hz, int dec) { return fix(p, udiv(hz, dec == 4 ? 100 : 1000), dec); }

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

static int dbm(sf_t *s) { return KM_RSSI_DBM(s->api->fd_read(0x67)); }

/* with the audio open: speaker off (1) while the receiver tunes about, back on (0) once it has
   settled on the listening frequency again; a retune with the amp on buzzes (Jeff, 2026-11-10) */
static void hush(sf_t *s, int on) {
  if (!s->audio || on == s->hush) return;
  s->hush = (uint8_t)on;
  s->api->fd_write(0x47, on ? AF_MUTE : s->af);
  wr(GPIOB + (on ? BRR : BSRR), SPK);
}

/* the level at hz in dBm, us after its tune (the wide filter) */
static int read_hz(sf_t *s, uint32_t hz, uint32_t us) {
  hush(s, 1);
  s->api->tune(hz, WIDE);
  s->api->delay_us(us);
  return dbm(s);
}

static int read_at(sf_t *s, int i, uint32_t us) { return read_hz(s, s->hz[i], us); }

static void audio(sf_t *s, int on) {
  if (on == s->audio && !s->hush) return;
  s->audio = (uint8_t)on;
  s->hush = 0;
  s->api->fd_write(0x47, on ? s->af : AF_MUTE);
  wr(GPIOB + (on ? BSRR : BRR), SPK);
}

/* the band's demodulator: AM (airband) with the AFC off and AM's AGC set-up, else FM as Aura
   had it (egzumer's and Aura's RADIO_SetModulation agree, docs/k6-afsweep.md) */
static void demod(sf_t *s) {
  int am = s->bd->am;
  const k6_api_t *api = s->api;
  s->af = (uint16_t)(am ? AF_AM : AF_FM);
  api->fd_write(0x73, (uint16_t)((s->r73 & ~0x10u) | (am ? 0x10u : 0)));
  api->fd_write(0x14, (uint16_t)(am ? 0 : s->r14));
  api->fd_write(0x49, (uint16_t)(am ? AM_49 : s->r49));
  if (s->audio && !s->hush) api->fd_write(0x47, s->af);
}

/* the receiver's gain: Aura's AGC (gx 0), or fixed at GAINS[gx - 1] (REG_7E bit 15, index 3
   = REG_13); the RSSI moves with it, so the settled floor is measured again */
static void gain(sf_t *s) {
  const k6_api_t *api = s->api;
  char *p = put(s->msg, "gain ");
  if (s->gx) {
    api->fd_write(0x13, GAINS[s->gx - 1]);
    api->fd_write(0x7E, (uint16_t)((s->r7e & 0x0FFFu) | 0xB000u));
    p = put(sint(p, GAIN_DB[s->gx - 1]), " dB");
  } else {
    api->fd_write(0x13, (uint16_t)s->r13);
    api->fd_write(0x7E, (uint16_t)s->r7e);
    p = put(p, "auto");
  }
  *p = 0;
  s->t_msg = s->now + MSG_MS;
}

static void note(sf_t *s, const char *t) {
  put(s->msg, t)[0] = 0;
  s->t_msg = s->now + MSG_MS;
}

/* ---- the keypad ---- */

static int kp_scan(sf_t *s) {
  int ph, k = 0;
  for (ph = 0; ph < 6 && !k; ph++) {
    uint32_t c, col;
    wr(GPIOB + BSRR, ROWS_B);
    if (ph) wr(GPIOB + BRR, 1u << ROWPIN[ph]);
    s->api->delay_us(5);
    c = rd(GPIOC + IDR);
    if (!(c & (1u << 13))) col = 0;
    else if (!(c & (1u << 14))) col = 1;
    else if (!(rd(GPIOF + IDR) & (1u << 6))) col = 2;
    else if (!(rd(GPIOB + IDR) & (1u << 14))) col = 3;
    else continue;
    k = KEYS[4 * ph + col];
  }
  wr(GPIOB + BSRR, ROWS_B);
  return k;
}

/* a key event: the key once it reads the same twice 10 ms apart; UP and DOWN repeat */
static int kp_event(sf_t *s) {
  int k;
  if (s->now - s->t_key < 10) return 0;
  s->t_key = s->now;
  k = kp_scan(s);
  if (k != s->raw) {
    s->raw = (uint8_t)k;
    return 0;
  }
  if (k != s->key) {
    s->key = (uint8_t)k;
    s->t_rep = s->now + 400;
    return k;
  }
  if ((k == 'U' || k == 'D') && (int32_t)(s->now - s->t_rep) >= 0) {
    s->t_rep = s->now + 120;
    return k;
  }
  return 0;
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

/* the step around bin i (Hz) */
static uint32_t sp(sf_t *s, int i) {
  int a = dist(i);
  return L_HZ * (a < C ? s->off[a + 1] - s->off[a] : s->off[C] - s->off[C - 1]);
}

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
    if (!s->hz[j]) continue;
    d = read_at(s, j, CHECK_US);
    if (d < a) b = a, a = d;
    else if (d < b) b = d;
  }
  if (a == 999) a = read_at(s, C, CHECK_US);   /* (a band edge: the middle is always in it) */
  s->sfl = b < 999 ? b : a;
  s->t_sfl = s->api->ms();
  s->tuned = 0;
}

/* after the bar moves: listen there afresh (monitor: just retune) */
static void moved(sf_t *s) {
  s->tuned = 0;
  if (!s->mon) {
    audio(s, 0);
    s->parked = 0;
    s->check = 1;
  }
}

/* the middle on hz (in the band, on its channel raster) and the scale around it; each bin keeps
   the loudest old bin whose loudest read falls in its range */
static void recentre(sf_t *s, uint32_t hz) {
  const band_t *b = s->bd;
  uint32_t old[NV], lo, hi;
  uint8_t lv[NV];
  int i, j;
  if (hz < b->lo) hz = b->lo;
  if (hz > b->hi) hz = b->hi;
  s->cen = b->lo + udiv(hz - b->lo + s->ch / 2, s->ch) * s->ch;
  if (s->cen > b->hi) s->cen -= s->ch;
  for (i = 0; i < NV; i++) {
    uint32_t o = L_HZ * s->off[dist(i)], f = i < C ? s->cen - o : s->cen + o;
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
  s->pend = 0;
  s->full = 0;
  s->refine = 0;
  measure_sfl(s);
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
  p = mhz(p, s->cen, 4);
  *p++ = ' ';
  p = sint(p, s->sel_dbm);
  *p++ = ' ';
  say_end(s, sint(p, s->sfl));
}

/* bin j's channel: the middle frequency its loudest read would make */
static uint32_t chan(sf_t *s, int j) {
  uint32_t lo = s->bd->lo;
  return lo + udiv(s->pk[j] - lo + s->ch / 2, s->ch) * s->ch;
}

/* the first peak from bin j on, going d (+1 up, -1 down) that a settled read confirms (SQ_DB over
   the settled floor; bench run 1 on 2026-11-08 stopped on -103 to -122 dBm bumps without it)
   on a channel other than the middle's: the middle moves there; 1 if found */
static int seek(sf_t *s, int d, int j) {
  for (; j >= 0 && j < NV; j += d)
    if (is_peak(s, j) && chan(s, j) != s->cen) {
      int v = read_hz(s, chan(s, j), CHECK_US);
      s->tuned = 0;
      if (v < s->sfl + SQ_DB) continue;   /* a noise bump in the quick reads: not a station */
      recentre(s, s->pk[j]);
      s->sel_dbm = v;
      s->refine = 1;
      moved(s);
#ifdef SURF_AUTO_S
      say_sel(s);                         /* the bench: the read that confirmed it */
#endif
      return 1;
    }
  return 0;
}

/* no peak left on screen going d: the scale's last bin that way becomes the middle, and it looks
   again after the next sweep; at the band's edge, give up (the bench build wraps) */
static void hunt(sf_t *s, int d) {
  int e = d > 0 ? NV - 1 : 0;
  while (e != C && !s->hz[e]) e -= d;
  if (e != C && chan(s, e) != s->cen) {
    recentre(s, s->hz[e]);
    s->pend = d;
    s->sj = C + d;
    moved(s);
  } else {
    note(s, "no peak");
#ifdef SURF_AUTO_S
    window(s, s->bd->lo);
#else
    /* none to the band's edge: back where the search began (Jeff, 2026-11-10) */
    if (s->home != s->cen) {
      recentre(s, s->home);
      moved(s);
    }
#endif
  }
}

static void jump(sf_t *s, int d) {
  if (!s->pend) s->home = s->cen;   /* (a press while it hunts keeps the first start) */
  if (!s->full) {               /* the scale not swept yet: look after its first sweep */
    s->pend = d;
    s->sj = C + d;
  } else if (!seek(s, d, C + d)) {
    hunt(s, d);
  }
}

/* the middle to f (in the band) and listen there; a side key's search stops */
static void go_to(sf_t *s, uint32_t f) {
  if (f < s->bd->lo || f > s->bd->hi) return;
  s->pend = 0;
  recentre(s, f);
  moved(s);
}

/* a new band: its default step and span, AM or FM, the middle on its centre */
static void band(sf_t *s, const band_t *b) {
  s->bd = b;
  s->st = b->st;
  s->si = b->si;
  demod(s);
  window(s, (b->lo + b->hi) >> 1);
}

static void on_key(sf_t *s, int k) {
  switch (k) {
  case 'U': go_to(s, s->cen + s->ch); break;
  case 'D': go_to(s, s->cen - s->ch); break;
  case '5':
    go_to(s, (s->bd->lo + s->bd->hi) >> 1);
    break;
  case 'A': jump(s, 1); break;
  case 'B': jump(s, -1); break;
  case 'N': band(s, s->bd + 1 == BANDS + NBANDS ? BANDS : s->bd + 1); break;
  case '#':                           /* the next channel step (a new band resets it) */
    s->st = (uint8_t)(s->st + 1 == NSTEPS ? 0 : s->st + 1);
    s->ch = STEPS[s->st];             /* (the middle stays; UP / DOWN go onto the new raster) */
    note(s, "step");
    stepn(s->msg + 5, s->ch)[0] = 0;
    s->msg[4] = ' ';
    break;
  case '7':                           /* fixed gain down (from auto: 6 dB under the top) */
  case '9':                           /* fixed gain up (from auto: the top) */
    if (!s->gx) s->gx = k == '7' ? 2 : 1;
    else if (k == '7' ? s->gx < NGAINS : s->gx > 1) s->gx = (uint8_t)(s->gx + (k == '7' ? 1 : -1));
    gain(s);
    measure_sfl(s);
    s->stale = 1;
    moved(s);
    break;
  case '8':                           /* the receiver's own AGC again */
    s->gx = 0;
    gain(s);
    measure_sfl(s);
    s->stale = 1;
    moved(s);
    break;
  case '1':
  case '3':
    if (k == '1' ? s->si == 0 : s->si == NSPANS - 1) break;
    s->si = (uint8_t)(s->si + (k == '1' ? -1 : 1));
    lens(s);
    recentre(s, s->cen);
    s->pend = 0;
    break;
  case '*':
    s->mon ^= 1;
    s->tuned = 0;
    s->parked = s->mon;               /* (monitor: park_step opens the audio once tuned) */
    if (!s->mon) moved(s);
    break;
  case '0':
    s->hold ^= 1;
    break;
  case 'M': say_sel(s); break;
  }
}

/* ---- sweeping ---- */

/* bin i's quick level: the loudest of reads SUB_HZ apart across its range (the wide filter's
   width: a coarse bin far out would miss a carrier between two bins), and where it was */
static void sweep_bin(sf_t *s, int i) {
  uint32_t lo, hi, f, at = s->hz[i];
  int r, o = s->lvl[i], v;
  cov(s, i, &lo, &hi);
  if (hi - lo <= SUB_HZ) {                 /* the lens and bins up to 20 kHz wide: one read */
    r = read_at(s, i, SETTLE_US);
  } else {
    for (r = -999, f = lo + SUB_HZ / 2; f < hi; f += SUB_HZ)
      if (f >= s->bd->lo && f <= s->bd->hi && (v = read_hz(s, f, SETTLE_US)) > r) r = v, at = f;
    if (r == -999) r = read_at(s, i, SETTLE_US);
  }
  if (r + 174 >= o || !o || s->stale) s->pk[i] = at;
  r += 174;
  if (r < 1) r = 1;
  /* (the first sweep after a move replaces the levels the old scale lent it) */
  s->lvl[i] = (uint8_t)(r >= o || !o || s->stale ? r : (o - r > DECAY_DB ? o - DECAY_DB : r));
}

/* the quartile of the scale's known bars */
static void floor_q(sf_t *s) {
  uint8_t t[NB];
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

static void sweep_done(sf_t *s) {
  s->sweeps++;
  s->full = 1;
  s->stale = 0;
  floor_q(s);
  if (s->refine) {
    /* after a jump: the loudest bin of the lens, if that is another channel, is the station
       (the bin the jump came from may have been a coarse one far out) */
    int i, b = C;
    s->refine = 0;
    for (i = C - KI; i <= C + KI; i++)
      if (s->hz[i] && s->lvl[i] > s->lvl[b]) b = i;
    if (b != C && s->lvl[b] > s->lvl[C] + 1 && chan(s, b) != s->cen) {
      recentre(s, s->pk[b]);
      moved(s);
    }
  }
  if (s->pend) {
    int d = s->pend, j = s->sj;
    s->pend = 0;
    if (j < 0) j = 0;
    if (j >= NV) j = NV - 1;
    if (!seek(s, d, j)) hunt(s, d);
  }
}

/* a settled read of the bar's channel: opens the audio on a signal */
static void check_bar(sf_t *s) {
  int d, i;
  s->check = 0;
  s->t_chk = s->api->ms();
  d = s->sel_dbm = read_at(s, C, CHECK_US);
  s->tuned = 1;
  if (d < s->sfl) s->sfl = d;
  if (d >= s->sfl + SQ_DB) {
    if (s->stale) {
      /* the scale just moved: sweep it once before the audio opens (no sweeps while it plays) */
      for (i = 0; i < NV; i++)
        if (s->hz[i]) sweep_bin(s, i);
      s->next = 0;
      sweep_done(s);
      if (s->check) return;             /* that moved the middle again: check there */
      s->tuned = 0;
    }
    s->parked = 1;
    s->t_open = s->t_full = s->api->ms();
    audio(s, 1);
  }
}

static void sweep_step(sf_t *s) {
  if (s->hz[s->next]) {
    sweep_bin(s, s->next);
    s->tuned = 0;
  }
  if (++s->next >= NV) {
    s->next = 0;
    sweep_done(s);
    if (s->now - s->t_sfl >= SFL_MS) measure_sfl(s);
  }
  if (s->api->ms() - s->t_chk >= CHK_MS) s->check = 1;
}

static void park_step(sf_t *s) {
  int d;
  if (!s->tuned) {
    d = read_at(s, C, CHECK_US);
    s->tuned = 1;
    if (s->mon) audio(s, 1);
    hush(s, 0);
  } else {
    s->api->delay_us(PARK_US);
    d = dbm(s);
  }
  s->sel_dbm = d;
  if (d >= s->sfl + SQ_DB - HYST_DB) s->t_open = s->now;
  if (!s->mon && s->now - s->t_open > HANG_MS) {
    audio(s, 0);
    s->parked = 0;
    return;
  }
  if (!s->hold && (s->stale || (!s->mon && s->now - s->t_full >= REFRESH_MS))) {   /* (stale: a zoom) */
    int i;
    for (i = 0; i < NV; i++)          /* (read_hz hushes the speaker; the next read unhushes it) */
      if (s->hz[i]) sweep_bin(s, i);
    sweep_done(s);
    s->tuned = 0;
    s->t_full = s->api->ms();
  }
}

/* ---- the screen ---- */

static void px(uint8_t *b, int x, int r) {
  if ((unsigned)x < 128) b[16 * r + (x >> 3)] |= (uint8_t)(0x80 >> (x & 7));
}

/* bin i's two pixels in row r */
static void pair(uint8_t *b, int i, int r) { b[16 * r + (i >> 2)] |= (uint8_t)(0xC0 >> (2 * (i & 3))); }

/* the explorer's repeater table (c/k6/rt.h, level 5's fl_read): every valid record's rx_hz and
   slot, read once at the start (1024 reads of 8 bytes, ~0.1 s) */
static void rt_load(sf_t *s) {
  uint32_t r[2];
  int i;
  for (i = 0; i < RT_SLOTS && s->nk < NK; i++) {
    s->api->fl_read(RT_BASE + 64u * (uint32_t)i, r, 8);   /* (in range: it can't refuse) */
    if ((uint8_t)r[0] == 0xFC && r[1]) {
      s->khz[s->nk] = r[1];
      s->kslot[s->nk++] = (uint16_t)i;
    }
  }
}

/* line 5 on a known repeater: its call, offset (MHz) and TX tone, from its record */
static char *rt_info(sf_t *s, int slot) {
  uint8_t r[44];               /* record bytes 8..51 */
  char *p = s->ln;
  int32_t off;
  int i, tone;
  s->api->fl_read(RT_BASE + 64u * (uint32_t)slot + 8, r, 44);
  for (i = 32; i < 42 && r[i] > ' ' && r[i] < 0x7F; i++) *p++ = (char)r[i];
  off = (int32_t)(r[0] | r[1] << 8 | r[2] << 16 | (uint32_t)r[3] << 24);
  if (off) {
    *p++ = ' ';
    *p++ = off < 0 ? '-' : '+';
    p = fix(p, udiv((uint32_t)(off < 0 ? -off : off) + 50000, 100000), 1);
  }
  tone = r[6] | r[7] << 8;
  if (tone && !(tone & 0x8000)) {
    *p++ = ' ';
    p = fix(p, (uint32_t)tone, 1);
  }
  return p;
}

/* bin i's bar height in pixels, 1..BARH: the quick dB over the window's floor, 3/4 px each */
static int bar_h(sf_t *s, int i) {
  int v = s->lvl[i], h = s->qfl && v > s->qfl ? ((v - s->qfl) * 3) >> 2 : 0;
  if (h < 1) h = 1;
  if (h > BARH) h = BARH;
  return h;
}

static void draw(sf_t *s) {
  const k6_api_t *api = s->api;
  uint8_t *b = s->bmp;
  int i, r, x, k;
  char *p;
  s->t_flush = s->now;
  for (i = 0; i < 16 * ROWS; i++) b[i] = 0;
  for (i = 0; i < NV; i++) {
    if (!s->hz[i]) continue;
    for (r = AX - bar_h(s, i); r < AX; r++) pair(b, i, r);
    if (is_peak(s, i)) pair(b, i, BAR0 - 2);
    pair(b, i, AX);                       /* the axis line */
  }
  for (i = 0, k = -1; i < s->nk; i++) {   /* known repeaters: a mark in row 0 */
    int j = bin_of(s, s->khz[i]);
    if (j < 0) continue;
    pair(b, j, 0);
    if (j == C) k = s->kslot[i];
  }
  x = 2 * C;
  px(b, x, AX + 1), px(b, x + 1, AX + 1), px(b, x, AX + 2), px(b, x + 1, AX + 2);
  for (i = 0; i < (int)sizeof TICKS / 2; i++) {   /* the axis's ticks, each side of the middle */
    uint32_t o = 1000u * TICKS[i];
    int j, e, big = TICKS[i] == 20 || TICKS[i] == 100 || TICKS[i] == 1000;
    if (o > U_HZ * SPANS[s->si]) break;
    for (e = -1; e <= 1; e += 2) {
      j = bin_of(s, e < 0 ? s->cen - o : s->cen + o);
      if (j < 0) continue;
      px(b, 2 * j + (e > 0), AX + 1);
      if (big) px(b, 2 * j + (e > 0), AX + 2);
    }
  }
  for (r = 0; r < 3; r++)
    for (i = x - 2 + r; i <= x + 3 - r; i++) px(b, i, r + 1);

  /* "SURF 70cm 25k", or the fixed gain in place of SURF: "G-12 70cm 25k" */
  if (s->gx) p = sint(put(s->ln, "G"), GAIN_DB[s->gx - 1]);
  else p = put(s->ln, "SURF");
  *p++ = ' ';
  p = put(p, s->bd->name);
  *p++ = ' ';
  p = stepn(p, s->ch);
  if (s->mon) p = put(p, " MON");
  else if (s->hold) p = put(p, " HLD");
  *p = 0;
  api->lcd_text(0, s->ln);
  p = mhz(s->ln, s->cen, 4);
  *p++ = ' ';
  p = sint(p, s->sel_dbm);
  p = put(p, "dBm");
  if (s->audio) p = put(p, " RX");
  *p = 0;
  api->lcd_text(1, s->ln);
  if (k >= 0) {
    p = rt_info(s, k);
  } else {
    /* the axis's labels: the span at each end, the lens's edges under their ticks
       ("-1M   -20k +20k   +1M") */
    char t[8], *e = t;
    for (i = 0; i < 21; i++) s->ln[i] = ' ';
    s->ln[0] = '-';
    e = spanx(t, U_HZ * SPANS[s->si]);
    for (i = 0; t + i < e; i++) s->ln[1 + i] = t[i], s->ln[21 - (e - t) + i] = t[i];
    s->ln[20 - (e - t)] = '+';
    put(s->ln + 6, "-20k +20k");
    p = s->ln + 21;
  }
  *p = 0;
  api->lcd_text(5, (int32_t)(s->t_msg - s->now) > 0 ? s->msg : s->ln);
  api->lcd_wf(b, ROWS, 0);
  api->lcd_flush();
}

int k6_main(const k6_api_t *api) {
  sf_t st, *s = &st;
  uint8_t bmp[16 * ROWS];
  uint32_t n;
  uint8_t *z = (uint8_t *)s;
  unsigned i;
  int k;
  char *p;
  if (api->level < 6) return -3;
  for (i = 0; i < sizeof st; i++) z[i] = 0;
  s->api = api;
  s->bmp = bmp;
  s->r47 = api->fd_read(0x47);
  s->r13 = api->fd_read(0x13);
  s->r14 = api->fd_read(0x14);
  s->r49 = api->fd_read(0x49);
  s->r73 = api->fd_read(0x73);
  s->r7e = api->fd_read(0x7E);
  s->odr = rd(GPIOB + ODR) & SPK;
  s->af = AF_FM;
  s->audio = 1;
  audio(s, 0);
  {
    uint32_t hz = api->rx_hz();
    s->bd = &BANDS[2];
    for (i = 0; i < NBANDS; i++)
      if (hz >= BANDS[i].lo && hz <= BANDS[i].hi) s->bd = &BANDS[i];
    s->st = s->bd->st;
    s->si = s->bd->si;
    s->now = api->ms();
    rt_load(s);
    demod(s);
    window(s, hz);
    p = put(s->ln, "surf ");
    p = put(p, s->bd->name);
    *p++ = ' ';
    p = mhz(p, hz, 3);
    *p++ = ' ';
    say_end(s, stepn(p, s->ch));
  }
  s->t_flush = api->ms() - FLUSH_MS;
  for (;;) {
    s->now = api->ms();
    k = kp_event(s);
#ifdef SURF_AUTO_S
    if (!k && !s->pend && !s->check && s->now - s->t_auto >= SURF_AUTO_MS) {
      k = 'A';
      s->t_auto = s->now;
    }
    if (s->now >= SURF_AUTO_S * 1000u) break;
#endif
    if (k == 'X') break;
    if (k) on_key(s, k);
    if (s->check) check_bar(s);
    else if (s->parked) park_step(s);
    else sweep_step(s);
    s->now = api->ms();
    if (s->now - s->t_flush >= FLUSH_MS) draw(s);
  }
  /* put back the audio path, the demodulator and the gain; wait (up to 3 s) for the keys' release */
  api->fd_write(0x47, (uint16_t)s->r47);
  api->fd_write(0x13, (uint16_t)s->r13);
  api->fd_write(0x14, (uint16_t)s->r14);
  api->fd_write(0x49, (uint16_t)s->r49);
  api->fd_write(0x73, (uint16_t)s->r73);
  api->fd_write(0x7E, (uint16_t)s->r7e);
  wr(GPIOB + (s->odr ? BSRR : BRR), SPK);
  for (i = 0, n = 0; i < 300 && n < 5; i++) {
    n = kp_scan(s) ? 0 : n + 1;
    api->delay_us(10000);
  }
#ifdef SURF_AUTO_S
  /* the last bargraph, for the bench: "bar <middle MHz> <span> lens", then the bins, 32 a line,
     a letter a bin: its height a (1 px) to x (24 px), '-' outside the band; the screen shows it too */
  draw(s);
  p = put(s->ln, "bar ");
  p = mhz(p, s->cen, 4);
  *p++ = ' ';
  p = stepn(p, U_HZ * SPANS[s->si]);
  say_end(s, put(p, " lens"));
  for (p = s->ln, k = 0; k < NV;) {
    *p++ = s->hz[k] ? (char)('a' - 1 + bar_h(s, k)) : '-';
    if (!(++k & 31) || k == NV) say_end(s, p), p = s->ln;
  }
#endif
  p = put(s->ln, "end sw=");
  say_end(s, fix(p, (uint32_t)s->sweeps, 0));
  return s->sweeps;
}
