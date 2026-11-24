/* uv21_surf.c: band surfing on the UV-21 Pro, its main screen (Jeff, 2026-11-24). Receive only.

   The K6's SURF (c/k6/mod/surf.c, Jeff's idea of 2026-11-08) turned on its side for the UV-21's
   tall 128x160 colour screen (Jeff, 2026-11-24: "SURF should be rotated with the higher
   frequencies at the top of the display"). Frequency runs down the screen, the highest at the
   top: 63 bins of 2 rows each, the listening frequency always the middle one (bin C), on the
   same logarithmic "lens" scale as the K6 (the 8th bin each side +-20 kHz, the 31st the span).
   Each bin's bar grows to the right from an axis line, with the log-paper ticks and the span's
   labels to its left; a caret (a white dash) right of the bar marks a peak heard lately, further
   right the more often it was heard, and falls back toward the axis with time.

   Keys (Jeff, 2026-11-24, with more to come; the rest as on the K6):
     UP / DOWN       the listening frequency one channel up / down (held: repeats)
     top side key    jump to the next caret above (a higher frequency)
     bottom side key jump to the next caret below
     middle side key next band: 2m, 1.25m, 70cm, GMRS, MAR/WX, AIR, USER
     A/B             how fast the carets fall: 1 min, 5 min, 20 min, 15 s (a held A/B could
                     hand a playing signal to VFO B, as flux_radio's K6 SURF does: not yet)
     VFO/MR          type a custom range (USER): low MHz, #, high MHz, MENU (16 MHz at most)
     #               next channel step; held: the CW wake listens on the middle's frequency
     1 / 3           zoom in / out (+-100 kHz to +-25 MHz)
     4               the demodulator: FM, DSB, AM
     5               the band's centre
     7 / 9 / 8       fixed gain down / up, the receiver's own AGC
     *               monitor (audio open, the chip's squelch open)
     0               hold (no refresh sweeps while a signal plays)
     MENU            say the bar's frequency and level on the serial line
     EXIT            leave SURF (to the firmware's VFO screen)

   The scale, the sweep of a bin, the floor, the carets and the jumps are the K6's own code,
   shared through c/common/surf_core.inc; the one-receiver state machine (listen first, sweep
   while the bar's channel is quiet, park and play on a signal, hush on every retune) is the
   K6 module's, copied from c/k6/mod/surf.c. What is the UV-21's: the screen, the keys (the
   firmware's keypad driver hands them over) and the CW wake below.

   CW wake (Jeff, 2026-11-24: "Still should detect CW wake signal at a configurable frequency
   but at this point on 440.850"): the K6's explorer wakes on a CW ID there (xs_wake: a read 20 dB
   over a floor that rises 1 dB a quiet read, or over -60 dBm). The UV-21 has one receiver, so
   SURF goes to api->wake_hz for one settled read every WAKE_MS (WAKE_PARK_MS while a signal
   plays, which mutes it for ~15 ms), and a wake is 2 hits in the last 4 looks: a CW ID of a few
   seconds can't slip through its gaps. It says "wake 440.8500 -61 -114" and calls api->wake,
   shows WAKE HEARD on the bottom line, then rests WAKE_REST_MS. Nothing shows until then (Jeff,
   2026-11-24: the standing "WAKE 440.850" was confusing; "WAKE HEARD" is fine). A long press of # moves it to the middle's frequency.

   Lines (api->say): "surf 70cm 440.850 25k", "sel 441.2000 -71 -127" (MENU), "wake ...",
   "end sw=N". Never transmits; the firmware filters every register write as well.
   SPDX-License-Identifier: MIT */
#include <stdint.h>
#include "uv21_surf.h"

#define SF_API uv_api_t
#define ROWS 1                  /* (the core's K6 bitmap: not used here) */
#define NK 1                    /* (the explorer's repeater table: not on the UV-21 yet) */
#define SURF_USER_LO 470000000u
#define SURF_USER_HI 608000000u
#define USER_MIN 18000000u      /* the BK4829's receive range, as the K6's FD6818 */
#define USER_MAX 630000000u
#define ED_LEN 7
#define SF_EXTRA                                                                          \
  band_t ub;                    /* USER */                                                \
  uint8_t ed, en[2];            /* VFO/MR: the field being typed (1 low, 2 high), lengths */ \
  char eb[2][ED_LEN + 1];                                                                  \
  uint32_t t_wake, t_rest;      /* the last wake look; no looks before t_rest */          \
  int wfl;                      /* the wake frequency's floor (999 none yet) */           \
  uint8_t whist;                /* the last looks, 1 a hit, newest in bit 0 */            \
  uint8_t wseen;                /* a wake was heard: WAKE HEARD on the bottom line */     \
  uint8_t sl[NV];               /* the screen: each bin's bar length as drawn (255 never) */ \
  uint8_t scx[NV];              /* its caret's x - BX as drawn (0 none) */                \
  uint8_t scol[NV];             /* its bar's colour as drawn */                           \
  uint32_t scen;                /* the axis drawn for this middle and span */             \
  uint8_t ssi, sdirty;                                                                    \
  uint8_t saud, dpend;          /* the squelch as last seen; a level to show once settled */ \
  uint32_t dcen, t_dq;          /* the middle as last seen; when the squelch or it moved */ \
  int sdbm;                     /* the level shown */                                     \
  char st0[34], st1[12], st2[34], st3[14];   /* the text lines as drawn */
#include "../../../c/common/surf_core.inc"
/* (the core's span name: the K6 labels with it; the UV-21's labels are absolute) */
static char *(*const keep_spanx)(char *, uint32_t) __attribute__((unused)) = spanx;

#define CHK_MS 70
#define PARK_US 20000
#define REFRESH_MS 5000
#define FLUSH_MS 200
#define WAKE_MS 500
#define WAKE_PARK_MS 1500
#define WAKE_REST_MS 60000
#define WAKE_DB 20
#define WAKE_ABS (-60)
#define WAKE_FLOOR_MIN (-135)  /* the wake floor never below: a read under it is a glitch (a retune not
                                   settled), and a floor pinned there makes the next noise a hit */
#define DBM_SETTLE_MS 100      /* the level is shown this long after the squelch or the middle moves */

/* ---- the screen: 128 x 160 ---- */
#define TITLE_H 9               /* the title bar: one line of 5x5 */
#define FREQ_Y 11               /* the listening frequency and its level, 6x10 */
#define BY 24                   /* the top bin's first row: bin NV-1 (the highest) */
#define BINY(i) (BY + 2 * (NV - 1 - (i)))
#define LX 0                    /* the span's labels, 5x5, 5 characters */
#define TX0 20                  /* the ticks: x 20-23 (major), 22-23 (minor) */
#define AXX 24                  /* the axis line */
#define BX 25                   /* the bars start here */
#define BW 100                  /* and reach BX + BW at most */
#define CW 3                    /* a caret: 3 x 2 */
#define MSG_Y 152               /* the bottom line, 5x5 */
#define DBX 60                  /* the level, right of the frequency on its line */

#define RGB(r, g, b) ((uint16_t)((((r) >> 3) << 11) | (((g) >> 2) << 5) | ((b) >> 3)))
#define BG RGB(0, 0, 0)
#define TBG RGB(16, 48, 96)
#define FG RGB(255, 255, 255)
#define DIM RGB(104, 104, 104)
#define HAIR RGB(48, 48, 48)
#define BAR RGB(0, 200, 90)
#define MID RGB(255, 200, 0)
#define LIVE RGB(255, 120, 40)
#define ALERT RGB(255, 64, 32)
#define CYAN RGB(110, 220, 255)

/* the bands, as the K6's (BANDS[2] 70 cm is where SURF starts unless start_hz is elsewhere) */
static const band_t BANDS[] = {
    {144000000u, 148000000u, 146000000u, ST_10K, 2, 0, "2m"},
    {222000000u, 225000000u, 223500000u, ST_10K, 2, 0, "1.25m"},
    {430000000u, 450000000u, 440000000u, ST_25K, 3, 0, "70cm"},
    {462550000u, 467725000u, 465137500u, ST_12K5, 4, 0, "GMRS"},
    {156000000u, 162550000u, 162475000u, ST_25K, 3, 0, "MAR/WX"},
    {118000000u, 137000000u, 127500000u, ST_25K, 3, 1, "AIR"}};
#define NBANDS 6

static int dbm(sf_t *s) { return ((int)((s->api->fd_read(0x67) & 0x1FF) >> 1)) - 174; }

static void hush(sf_t *s, int on) {
  if (!s->audio || on == s->hush) return;
  s->hush = (uint8_t)on;
  s->api->fd_write(0x47, on ? AF_MUTE : s->af);
  s->api->speaker(!on);
}

/* a key between reads: kept for the main loop (one at a time), so a long sweep doesn't lose it */
static void kp_peek(sf_t *s) {
  if (!s->qk) {
    int k = s->api->key();
    if (k) s->qk = (uint8_t)k;
  }
}

static int read_hz(sf_t *s, uint32_t hz, uint32_t us) {
  hush(s, 1);
  s->api->tune(hz, WIDE);
  s->api->delay_us(us);
  kp_peek(s);
  return dbm(s);
}

static void audio(sf_t *s, int on) {
  if (on == s->audio && !s->hush) return;
  s->audio = (uint8_t)on;
  s->hush = 0;
  s->api->fd_write(0x47, on ? s->af : AF_MUTE);
  s->api->speaker(on);
}

static void demod(sf_t *s) {
  int md = s->md;
  const uv_api_t *api = s->api;
  s->af = (uint16_t)(md == MD_AM ? AF_AM : md == MD_DSB ? AF_DSB : AF_FM);
  if (md == MD_DSB || s->zif) {
    s->zif = 1;
    api->fd_write(0x3D, (uint16_t)(md == MD_DSB ? 0 : s->r3d));
  }
  api->fd_write(0x73, (uint16_t)((s->r73 & ~0x10u) | (md != MD_FM ? 0x10u : 0)));
  if (s->audio && !s->hush) api->fd_write(0x47, s->af);
}

static void squelch(sf_t *s, int on) {
  const uv_api_t *api = s->api;
  int k;
  if (on)
    for (k = 0; k < 4; k++) s->sq[k] = api->fd_read(SQ_REGS[k]);
  api->fd_write(0x4D, on ? (uint16_t)((s->sq[0] & 0xFF00u) | 0xFFu) : s->sq[0]);
  api->fd_write(0x4E, on ? (uint16_t)((s->sq[1] & 0xFF00u) | 0xFFu) : s->sq[1]);
  api->fd_write(0x4F, on ? 0x7F7Fu : s->sq[2]);
  api->fd_write(0x78, on ? 0x0000u : s->sq[3]);
}

static void gain(sf_t *s) {
  const uv_api_t *api = s->api;
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

/* ---- the custom range (USER), as the K6's V ---- */

static void user_set(sf_t *s, uint32_t lo, uint32_t hi) {
  band_t *u = &s->ub;
  int i;
  u->lo = lo;
  u->hi = hi;
  u->home = lo + ((hi - lo) >> 1);
  u->st = ST_25K;
  for (i = 0; i < NSPANS - 1 && 2 * U_HZ * SPANS[i] < hi - lo; i++) {}
  u->si = (uint8_t)i;
  u->am = lo >= 108000000u && hi <= 137000000u;
  put(u->name, "USER")[0] = 0;
}

static uint32_t ed_hz(const char *t) {
  uint32_t v = 0;
  int fr = -1;
  for (; *t; t++) {
    if (*t == '.') {
      fr = 0;
    } else if (fr < 6) {
      v = 10 * v + (uint32_t)(*t - '0');
      if (fr >= 0) fr++;
    }
  }
  for (fr = fr < 0 ? 0 : fr; fr < 6; fr++) v *= 10;
  return v;
}

static int ed_done(sf_t *s) {
  uint32_t lo = ed_hz(s->eb[0]), hi = ed_hz(s->eb[1]);
  if (!s->en[1]) {
    hi = lo + 1000000u;
    lo = lo > USER_MIN + 1000000u ? lo - 1000000u : USER_MIN;
    if (hi > USER_MAX) hi = USER_MAX;
  }
  if (lo < USER_MIN || hi > USER_MAX || hi <= lo) return 0;
  if (hi - lo > SURF_USER_SPAN) {        /* (wider: the middle SURF_USER_SPAN of it) */
    lo += (hi - lo - SURF_USER_SPAN) / 2;
    hi = lo + SURF_USER_SPAN;
  }
  user_set(s, lo, hi);
  band(s, &s->ub);
  if (!s->en[1]) recentre(s, ed_hz(s->eb[0])), moved(s);
  {
    char *p = put(s->ln, "user ");
    p = mhz(p, lo, 3);
    *p++ = ' ';
    say_end(s, mhz(p, hi, 3));
  }
  return 1;
}

static void ed_key(sf_t *s, int k) {
  int f = s->ed - 1, n = s->en[f], i;
  char *t = s->eb[f];
  if (k >= '0' && k <= '9') {
    if (n < ED_LEN) t[n++] = (char)k;
  } else if (k == '*') {
    for (i = 0; i < n && t[i] != '.'; i++) {}
    if (i == n && n && n < ED_LEN) t[n++] = '.';
  } else if (k == 'D') {
    if (n) n--;
  } else if (k == '#') {
    s->ed = (uint8_t)(3 - s->ed);
  } else if (k == 'M' || k == 'V') {
    if (s->en[0] && ed_done(s)) s->ed = 0;
    else note(s, "bad range");
  } else if (k == 'X') {
    s->ed = 0;
  }
  t[n] = 0;
  s->en[f] = (uint8_t)n;
}

static char *ed_line(sf_t *s, char *p) {
  p = put(p, "SET ");
  p = put(p, s->eb[0]);
  if (s->ed == 1) *p++ = '_';
  *p++ = '-';
  p = put(p, s->eb[1]);
  if (s->ed == 2) *p++ = '_';
  return p;
}

/* ---- listening and sweeping with one receiver (the K6 module's) ---- */

static void moved(sf_t *s) {
  s->tuned = 0;
  if (!s->mon) {
    audio(s, 0);
    s->parked = 0;
    s->check = 1;
  }
}

/* the CW wake: one settled look at wake_hz (see the top) */
static void wake_look(sf_t *s) {
  const uv_api_t *api = s->api;
  uint32_t hz = api->wake_hz;
  int d, hit;
  s->t_wake = s->now;
  if (!hz || (int32_t)(s->now - s->t_rest) < 0) return;
  if (hz == s->cen && s->parked) {
    d = s->sel_dbm;                      /* (parked on it: the parked reads are the look) */
  } else {
    d = read_hz(s, hz, CHECK_US);
    s->tuned = 0;                        /* (the next park or sweep read tunes back) */
  }
  if (s->wfl == 999) s->wfl = d < WAKE_FLOOR_MIN ? WAKE_FLOOR_MIN : d;
  hit = d >= s->wfl + WAKE_DB || d > WAKE_ABS;
  if (!hit) {
    if (d < s->wfl) s->wfl = d;
    else s->wfl++;
    if (s->wfl < WAKE_FLOOR_MIN) s->wfl = WAKE_FLOOR_MIN;
  }
  s->whist = (uint8_t)((s->whist << 1) | hit);
  if (((s->whist & 1) + (s->whist >> 1 & 1) + (s->whist >> 2 & 1) + (s->whist >> 3 & 1)) >= 2) {
    char *p = put(s->ln, "wake ");
    p = mhz(p, hz, 4);
    *p++ = ' ';
    p = sint(p, d);
    *p++ = ' ';
    say_end(s, sint(p, s->wfl));
    s->whist = 0;
    s->wseen = 1;
    s->t_rest = s->now + WAKE_REST_MS;
    api->wake(hz, d, s->wfl);
  }
}

static void on_key(sf_t *s, int k) {
  if (s->ed) {
    ed_key(s, k);
    return;
  }
  switch (k) {
  case 'V':
    s->ed = 1;
    s->en[0] = s->en[1] = 0;
    s->eb[0][0] = s->eb[1][0] = 0;
    break;
  case 'U': go_to(s, s->cen + s->ch); break;
  case 'D': go_to(s, s->cen - s->ch); break;
  case '5': go_to(s, s->bd->home); break;
  case 'A': jump(s, 1); break;            /* the top side key: the next peak up */
  case 'B': jump(s, -1); break;           /* the bottom one: the next peak down */
  case 'W':
    s->fall = (uint8_t)(s->fall + 1 == NFALL ? 0 : s->fall + 1);
    put(put(s->msg, "fall "), FALL[s->fall])[0] = 0;
    s->t_msg = s->now + MSG_MS;
    break;
  case 'N':                               /* the middle side key: the next band */
    band(s, s->bd == &s->ub ? BANDS : s->bd + 1 == BANDS + NBANDS ? &s->ub : s->bd + 1);
    break;
  case '#':
    s->st = (uint8_t)(s->st + 1 == NSTEPS ? 0 : s->st + 1);
    s->ch = STEPS[s->st];
    note(s, "step");
    stepn(s->msg + 5, s->ch)[0] = 0;
    s->msg[4] = ' ';
    break;
  case '#' | UV_LONG:                     /* the wake listens here now */
    ((uv_api_t *)s->api)->wake_hz = s->cen;   /* (the caller's, so it lasts past EXIT) */
    s->wfl = 999;
    s->whist = 0;
    s->t_rest = s->now;
    mhz(put(s->msg, "wake "), s->cen, cdec(s))[0] = 0;
    s->t_msg = s->now + MSG_MS;
    break;
  case '7':
  case '9':
    if (!s->gx) s->gx = k == '7' ? 2 : 1;
    else if (k == '7' ? s->gx < NGAINS : s->gx > 1) s->gx = (uint8_t)(s->gx + (k == '7' ? 1 : -1));
    gain(s);
    measure_sfl(s);
    s->stale = 1;
    moved(s);
    break;
  case '8':
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
    break;
  case '*':
    s->mon ^= 1;
    squelch(s, s->mon);
    s->tuned = 0;
    s->parked = s->mon;
    if (!s->mon) moved(s);
    break;
  case '0': s->hold ^= 1; break;
  case '4':
    s->md = (uint8_t)(s->md + 1 == NMD ? 0 : s->md + 1);
    demod(s);
    note(s, MD_NAME[s->md]);
    measure_sfl(s);
    s->stale = 1;
    moved(s);
    break;
  case 'M': say_sel(s); break;
  }
}

static void check_bar(sf_t *s) {
  int d, i;
  s->check = 0;
  s->t_chk = s->api->ms();
  d = s->sel_dbm = read_at(s, C, CHECK_US);
  s->tuned = 1;
  if (d < s->sfl) s->sfl = d;
  if (s->stale && (d >= s->sfl + SQ_DB || s->refine)) {
    for (i = C - KI; i <= C + KI && !s->qk; i++)
      if (bin_hz(s, i)) sweep_bin(s, i);
    if (s->qk) return;
    if (s->refine && refine_lens(s)) return;
    s->tuned = 0;
  }
  if (d >= s->sfl + SQ_DB) {
    s->stale = 0;
    s->parked = 1;
    s->t_open = s->t_full = s->api->ms();
    if (!s->full) s->t_full -= REFRESH_MS;
    audio(s, 1);
    pm_heard(s);
  }
}

static void sweep_step(sf_t *s) {
  if (bin_hz(s, s->next)) {
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
    hush(s, 0);
  } else {
    s->api->delay_us(PARK_US);
    d = dbm(s);
  }
  if (s->mon && !s->audio) audio(s, 1);
  s->sel_dbm = d;
  if (d >= s->sfl + SQ_DB) pm_heard(s);
  if (d >= s->sfl + SQ_DB - HYST_DB) s->t_open = s->now;
  if (!s->mon && s->now - s->t_open > HANG_MS) {
    audio(s, 0);
    s->parked = 0;
    return;
  }
  if (!s->hold && (s->stale || (!s->mon && s->now - s->t_full >= REFRESH_MS))) {
    int i;
    for (i = 0; i < NV && !s->qk; i++)
      if (bin_hz(s, i)) sweep_bin(s, i);
    if (!s->qk) sweep_done(s);
    s->tuned = 0;
    s->t_full = s->api->ms();
  }
}

/* ---- the screen ---- */

/* bin i's bar length, 1..BW px: 5/4 px per quick dB over the window's floor */
static int bar_len(sf_t *s, int i) {
  int v = s->lvl[i], h = s->qfl && v > s->qfl ? ((v - s->qfl) * 5) >> 2 : 0;
  if (h < 1) h = 1;
  if (h > BW) h = BW;
  return h;
}

static int streq(const char *a, const char *b) {
  while (*a && *a == *b) a++, b++;
  return *a == *b;
}

/* a text line, drawn only when it changed: clear its strip, then the text */
static void line(sf_t *s, char *drawn, const char *t, int x, int y, int w, int h, uint16_t fg, uint16_t bg,
                 int font) {
  if (!s->sdirty && streq(drawn, t)) return;
  put(drawn, t)[0] = 0;
  s->api->fill(x - 2, y, w, h, bg);
  s->api->text(x, y + (font ? 0 : 1), t, fg, bg, font);
}

/* an edge label: hz in MHz, rounded, to 0.1 MHz on spans of +-2.5 MHz and up, else 0.01 (a
   trailing 0 dropped): 5 characters in the label column, 6 reaching into the tick column */
static void edge_mhz(char *t, uint32_t hz, uint32_t span) {
  uint32_t d = span >= 2500000u ? 100000u : 10000u;
  char *e = fix(t, udiv(hz + d / 2, d), d == 100000u ? 1 : 2);
  if (d == 10000u && e[-1] == '0') e--;       /* 440.50 -> 440.5 */
  *e = 0;
}

/* the axis, its ticks and the labels: when the middle or the span moved */
static void axis(sf_t *s) {
  const uv_api_t *api = s->api;
  char t[12];
  int i, j;
  if (!s->sdirty && s->scen == s->cen && s->ssi == s->si) return;
  s->scen = s->cen;
  s->ssi = s->si;
  api->fill(LX, BY - 3, AXX - LX, 2 * NV + 5, BG);
  for (i = 0; i < NV; i++)               /* the axis line beside each bin in the band */
    api->fill(AXX, BINY(i), 1, 2, bin_hz(s, i) ? DIM : BG);
  for (i = 0; i < (int)(sizeof TICKS / 2); i++) {
    uint32_t o = 1000u * TICKS[i];
    int e2, big = TICKS[i] == 20 || TICKS[i] == 100 || TICKS[i] == 1000 || TICKS[i] == 10000;
    if (o > U_HZ * SPANS[s->si]) break;
    for (e2 = -1; e2 <= 1; e2 += 2) {
      j = bin_of(s, e2 < 0 ? s->cen - o : s->cen + o);
      if (j < 0) continue;
      api->fill(big ? TX0 : TX0 + 2, BINY(j) + (e2 > 0 ? 0 : 1), big ? 4 : 2, 1, DIM);
    }
  }
  /* the middle: a pointer in the tick column */
  api->fill(TX0, BINY(C) - 1, 1, 4, MID);
  api->fill(TX0 + 1, BINY(C), 2, 2, MID);
  /* labels: the top and bottom bins' frequencies in MHz (Jeff, 2026-11-24: "show e.g. 463.5
     and 461.0"), the lens's edges by their ticks */
  {
    uint32_t sp = U_HZ * SPANS[s->si];
    edge_mhz(t, s->cen + sp, sp);
    api->text(LX, BINY(NV - 1) - 2, t, DIM, BG, 0);
    if (s->cen > sp) {
      edge_mhz(t, s->cen - sp, sp);
      api->text(LX, BINY(0) - 2, t, DIM, BG, 0);
    }
  }
  api->text(LX, BINY(C + KI) - 2, "+20K", DIM, BG, 0);
  api->text(LX, BINY(C - KI) - 2, "-20K", DIM, BG, 0);
}

static void draw(sf_t *s) {
  const uv_api_t *api = s->api;
  uint8_t *top = s->tmp;
  char *p, t[34];
  int i, k, r;
  s->t_flush = s->now;

  /* the title: the band, its step, MON / HLD, a fixed gain, and the demodulator at the right */
  p = put(t, "SURF ");
  p = put(p, s->bd->name);
  *p++ = ' ';
  p = stepn(p, s->ch);
  if (s->mon) p = put(p, " MON");
  else if (s->hold) p = put(p, " HLD");
  if (s->gx) p = put(sint(put(p, " G"), GAIN_DB[s->gx - 1]), "");
  for (r = 0; MD_NAME[s->md][r]; r++) {}
  while (p < t + 31 - r) *p++ = ' ';
  p = put(p, MD_NAME[s->md]);
  *p = 0;
  line(s, s->st0, t, 2, 0, 128, TITLE_H, FG, TBG, 0);

  /* the listening frequency: redrawn only when it changes (Jeff, 2026-11-24) */
  p = mhz(t, s->cen, cdec(s));
  *p = 0;
  line(s, s->st1, t, 2, FREQ_Y, DBX - 2, 11, FG, BG, 1);

  /* its level: taken DBM_SETTLE_MS after the squelch opens or closes, or the middle moves, and
     left alone in between (Jeff, 2026-11-24: "draw this about 0.1 second after the squelch
     breaks, and again about 0.1 sec after it closes to allow settling") */
  if (s->dpend && s->now - s->t_dq >= DBM_SETTLE_MS) {
    s->dpend = 0;
    s->sdbm = s->sel_dbm;
  }
  p = sint(t, s->sdbm);
  p = put(p, "dBm");
  if (s->saud) p = put(p, " RX");
  *p = 0;
  line(s, s->st3, t, DBX, FREQ_Y, 128 - DBX + 2, 11, s->saud ? LIVE : FG, BG, 1);

  axis(s);

  /* the carets: each bin's highest remembered peak */
  for (i = 0; i < NV; i++) top[i] = 0;
  for (k = 0; k < s->npm; k++) {
    pm_t *e = &s->pm[k];
    int j;
    if (!e->h || e->bad || (j = bin_of(s, e->f)) < 0) continue;
    if (e->h > top[j]) top[j] = e->h;
  }
  /* the bars, each redrawn only when its length, colour or caret changed */
  for (i = 0; i < NV; i++) {
    int y = BINY(i), len = bin_hz(s, i) ? bar_len(s, i) : 0, cx = 0;
    uint8_t col = !bin_hz(s, i) ? 0 : i == C ? (s->audio ? 3 : 2) : 1;
    if (top[i]) {
      cx = 1 + (int)((uint32_t)top[i] * (BW - CW - 2) / HMAX);
      if (cx < len + 2) cx = len + 2;         /* (it rides on its bar, never inside it) */
      if (cx > BW - CW) cx = BW - CW;
    }
    if (!s->sdirty && s->sl[i] == len && s->scx[i] == cx && s->scol[i] == col) continue;
    s->sl[i] = (uint8_t)len;
    s->scx[i] = (uint8_t)cx;
    s->scol[i] = col;
    if (len) api->fill(BX, y, len, 2, col == 3 ? LIVE : col == 2 ? MID : BAR);
    api->fill(BX + len, y, BW + 3 - len, 2, BG);
    if (cx) api->fill(BX + cx, y, CW, 2, FG);
  }

  /* the bottom line: a message, the typing, WAKE HEARD once a wake was heard, else blank */
  p = t;
  if ((int32_t)(s->t_msg - s->now) > 0) p = put(t, s->msg);
  else if (s->ed) p = ed_line(s, t);
  else if (s->wseen) p = mhz(put(t, "WAKE HEARD  "), api->wake_hz, 3);
  *p = 0;
  for (p = t; *p; p++)
    if (*p >= 'a' && *p <= 'z') *p = (char)(*p - 32);
  line(s, s->st2, t, 2, MSG_Y, 128, 8, s->wseen ? CYAN : DIM, BG, 0);
  s->sdirty = 0;
}

uint32_t uv21_surf_state_size(void) { return (uint32_t)sizeof(sf_t); }

uint32_t uv21_surf(uv_api_t *api, void *state, uint32_t room) {
  sf_t *s;
  uint8_t *z = (uint8_t *)state;
  uint32_t n, hz;
  unsigned i;
  int k;
  char *p;
  if (room < sizeof(sf_t) + 8 * 16) return 0;
  for (i = 0; i < sizeof(sf_t); i++) z[i] = 0;
  s = (sf_t *)z;
  room -= sizeof(sf_t);
  s->api = api;
  s->fall = 1;
  s->pm = (pm_t *)(z + sizeof(sf_t));
  s->npm = room >> 3 < NPM ? (int)(room >> 3) : NPM;
  for (n = 0; n < (uint32_t)s->npm; n++) s->pm[n].h = 0;
  s->r47 = api->fd_read(0x47);
  s->r13 = api->fd_read(0x13);
  s->r73 = api->fd_read(0x73);
  s->r7e = api->fd_read(0x7E);
  s->r3d = api->fd_read(0x3D);
  s->af = AF_FM;
  s->audio = 1;
  audio(s, 0);
  s->wfl = 999;
  s->sdirty = 1;
  hz = api->start_hz;
  user_set(s, SURF_USER_LO, SURF_USER_HI);
  s->bd = hz >= s->ub.lo && hz <= s->ub.hi ? &s->ub : &BANDS[2];
  for (i = 0; i < NBANDS; i++)
    if (hz >= BANDS[i].lo && hz <= BANDS[i].hi) s->bd = &BANDS[i];
  if (hz < s->bd->lo || hz > s->bd->hi) hz = s->bd->home;
  s->st = s->bd->st;
  s->si = s->bd->si;
  s->md = s->bd->am ? MD_AM : MD_FM;
  s->now = api->ms();
  s->t_rest = s->now;
  demod(s);
  window(s, hz);
  p = put(s->ln, "surf ");
  p = put(p, s->bd->name);
  *p++ = ' ';
  p = mhz(p, hz, 3);
  *p++ = ' ';
  say_end(s, stepn(p, s->ch));
  api->fill(0, 0, 128, 160, BG);
  s->t_flush = api->ms() - FLUSH_MS;
  for (;;) {
    s->now = api->ms();
    if (api->poll()) break;
    pm_fall(s);
    k = s->qk ? s->qk : api->key();
    s->qk = 0;
    if (k == 'X' && !s->ed) break;
    if (k) {
      on_key(s, k);
      s->now = api->ms();
      draw(s);
    }
    if (s->check) check_bar(s);
    else if (s->parked) park_step(s);
    else sweep_step(s);
    s->now = api->ms();
    if (s->audio != s->saud || s->cen != s->dcen) {   /* the level is due once it settles */
      s->saud = s->audio;
      s->dcen = s->cen;
      s->dpend = 1;
      s->t_dq = s->now;
    }
    if (s->now - s->t_wake >= (s->parked ? WAKE_PARK_MS : WAKE_MS)) wake_look(s);
    if (s->now - s->t_flush >= FLUSH_MS || (s->dpend && s->now - s->t_dq >= DBM_SETTLE_MS)) draw(s);
  }
  api->fd_write(0x47, (uint16_t)s->r47);
  api->fd_write(0x13, (uint16_t)s->r13);
  api->fd_write(0x73, (uint16_t)s->r73);
  api->fd_write(0x7E, (uint16_t)s->r7e);
  if (s->zif) api->fd_write(0x3D, (uint16_t)s->r3d);
  if (s->mon) squelch(s, 0);
  api->speaker(0);
  p = put(s->ln, "end sw=");
  say_end(s, fix(p, (uint32_t)s->sweeps, 0));
  return s->cen;
}
