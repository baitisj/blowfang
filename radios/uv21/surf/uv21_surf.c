/* uv21_surf.c: band surfing on the UV-21 Pro, its main screen (Jeff, 2026-11-24). Receive only.

   The K6's SURF (c/k6/mod/surf.c, Jeff's idea of 2026-11-08) turned on its side for the UV-21's
   tall 128x160 colour screen (Jeff, 2026-11-24: "SURF should be rotated with the higher
   frequencies at the top of the display"). Frequency runs down the screen, the highest at the
   top: 63 bins of 2 rows each, the listening frequency always the middle one (bin C), on the
   same logarithmic "lens" scale as the K6 (the 8th bin each side +-20 kHz, the 31st the span).
   Each bin's bar grows to the right from an axis line, with the log-paper ticks and the span's
   labels to its left, coloured by its level over the floor (the heat colours, port 18); right of
   the bars, past a divider, the history strip carries those colours across, one column a sweep
   (see "the history strip" below); a caret (a white dash) right of the bar marks a peak heard lately, further
   right the more often it was heard, and falls back toward the axis with time.

   Keys (Jeff, 2026-11-24, with more to come; the rest as on the K6):
     UP / DOWN       the listening frequency one channel up / down (held: repeats)
     top side key    jump to the next caret above (a higher frequency)
     bottom side key jump to the next caret below
     middle side key next band: 2m, 1.25m, 70cm, GMRS, MAR/WX, AIR, CUSTOM; held: CUSTOM centred
                     on the middle's frequency, as wide as the band was (16 MHz at most)
     A/B             how fast the carets fall: 1 min, 5 min, 20 min, 15 s (a held A/B could
                     hand a playing signal to VFO B, as flux_radio's K6 SURF does: not yet)
     VFO/MR          type a custom range (CUSTOM): low MHz, #, high MHz, MENU (16 MHz at most)
     #               next channel step; held: TUNE, type one frequency (* the point, # or MENU
                     ends it: 446# is 446.000), and CUSTOM is centred on it, as wide as the band
                     was: the VFO, in SURF
     1 / 3           zoom in / out (+-100 kHz to +-25 MHz)
     4               the demodulator: FM, DSB, AM
     5               the band's centre
     7 / 9 / 8       fixed gain down / up, the receiver's own AGC
     *               monitor: the squelch open (the chip's too); the sweeps and the carets'
                     fall go on as usual (Jeff, 2026-11-24), so the audio hushes for each
                     refresh sweep and wake look: one receiver
     0               hold: no retunes at all, the carets stay put; the middle listens with the
                     squelch as usual, the side keys still jump. Independent of monitor (Jeff,
                     2026-11-24: "Scanning should stop when hold is active")
     MENU            say the bar's frequency and level on the serial line; held: the CW wake
                     listens on the middle's frequency
     EXIT            leaves a typed range (SURF is the only screen since port 14: no VFO)

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
   seconds can't slip through its gaps. No looks while hold keeps the receiver on the middle
   (port 16), unless the middle is the wake frequency: those reads are the looks. It says "wake 440.8500 -61 -114" and calls api->wake,
   shows WAKE HEARD on the bottom line, then rests WAKE_REST_MS. Nothing shows until then (Jeff,
   2026-11-24: the standing "WAKE 440.850" was confusing; "WAKE HEARD" is fine). A long press of MENU moves it to the middle's frequency
   (it was #, until long # became frequency entry).

   Lines (api->say): "surf 70cm 440.850 25k", "sel 441.2000 -71 -127" (MENU), "wake ...",
   "fe rx path hi" (6, the front end's switch), "end sw=N". Never transmits; the firmware filters every register write as well.
   SPDX-License-Identifier: MIT */
#include <stdint.h>
#include "uv21_surf.h"

#define SF_API uv_api_t
#ifndef UV21_HIST
#define UV21_HIST 1             /* the history strip right of the bars (port 18); 0 drops it */
#endif
#define HN 52                   /* the strip: this many sweeps, one a pixel column */
#if UV21_HIST
#define HIST_EXTRA                                                                        \
  uint8_t hring[(HN * 63 + 1) / 2];  /* the levels, a nibble each: column k, bin i at k*NV+i;   \
                                    0-14 the bin's level over the floor in 3 dB steps, 15 none */ \
  uint8_t hhead, hdirty, hsi;   /* the newest column; the strip to draw; the scale it's on */ \
  const void *hbd;              /* ... its band */                                        \
  uint32_t hcen;                /* ... its middle */                                      \
  int hsw;                      /* the sweeps counted when the newest column went in */
#else
#define HIST_EXTRA
#endif
#define ROWS 1                  /* (the core's K6 bitmap: not used here) */
#define NK 1                    /* (the explorer's repeater table: not on the UV-21 yet) */
#define SURF_USER_LO 470000000u
#define SURF_USER_HI 608000000u
#define USER_MIN 18000000u      /* the BK4829's receive range, as the K6's FD6818 */
#define USER_MAX 630000000u
#define ED_LEN 7
#define SF_EXTRA                                                                          \
  band_t ub;                    /* CUSTOM */                                                \
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
  int16_t hr[4];                /* hold: the last 4 reads on the middle */                \
  uint8_t hi;                   /* ... the next one goes here */                          \
  int hnf;                      /* ... and the middle's own noise floor, 1/16 dB (HNF_NONE) */ \
  uint32_t dcen, t_dq;          /* the middle as last seen; when the squelch or it moved */ \
  int sdbm;                     /* the level shown */                                     \
  char st0[34], st1[12], st2[34], st3[14];   /* the text lines as drawn */                \
  HIST_EXTRA
#define SF_CUSTOM               /* the core's TUNE and CUSTOM */
#define SF_RANGE                /* and its typed range (VFO/MR) */
#define SF_ED_CHANGED(s) if (!(s)->ed) (s)->sdirty = 1   /* (TUNE drew over the top: all again) */
#include "../../../c/common/surf_core.inc"
/* (the core's span name: the K6 labels with it; the UV-21's labels are absolute) */
static char *(*const keep_spanx)(char *, uint32_t) __attribute__((unused)) = spanx;

#define CHK_MS 70
#define PARK_US 20000
#define REFRESH_MS 5000
#define FLUSH_MS 200
#define WAKE_MS 500
#define WAKE_PARK_MS 1500
#define HNF_NONE 0x7FFF        /* hold: the middle's floor not measured yet */
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
#if UV21_HIST
#define BW 48                   /* and reach BX + BW at most */
#define BEND 74                 /* the bars' area ends before this column: the divider */
#define HX 76                   /* the history strip: HN columns from here, newest at the left */
#else
#define BW 100
#define BEND 128
#endif
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
#define RED RGB(255, 40, 40)
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

/* ---- the custom range (CUSTOM; the K6's USER), as the K6's V ---- */

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
  put(u->name, "CUSTOM")[0] = 0;   /* (the operator's name for it) */
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
  s->hnf = HNF_NONE;
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
  if (hz == s->cen && (s->parked || s->hold) && s->tuned) {
    d = s->sel_dbm;                      /* (listening on it: those reads are the look) */
  } else if (s->hold) {
    return;                              /* hold: no retunes at all (port 16) */
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
  case 'V': ed_start(s, 1); break;     /* type a range */
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
  case '#' | UV_LONG: ed_start(s, 3); break;   /* TUNE: CUSTOM centred on what's typed */
  case 'N' | UV_LONG:                     /* the middle side key, long: CUSTOM centred here */
    centre_custom(s, s->cen);
    break;
  case 'M' | UV_LONG:                     /* the wake listens here now */
    ((uv_api_t *)s->api)->wake_hz = s->cen;   /* (the caller's: it outlasts a run) */
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
  case '0':
    s->hold ^= 1;
    s->hnf = HNF_NONE;
    if (!s->audio) s->t_open = s->now - HANG_MS - 1;   /* (an old open doesn't reopen it) */
    if (!s->hold) moved(s);           /* (sweeping again: check the middle first) */
    break;
  case '4':
    s->md = (uint8_t)(s->md + 1 == NMD ? 0 : s->md + 1);
    demod(s);
    note(s, MD_NAME[s->md]);
    measure_sfl(s);
    s->stale = 1;
    moved(s);
    break;
  case 'M': say_sel(s); break;
  case '6': {                             /* the front end's next way to switch (see the .h) */
    static const char *const FE[] = {"rx path swd", "rx path hi", "rx path lo", "rx path hi+56"};
    note(s, FE[s->api->front_end(1) & 3]);
    put(put(s->ln, "fe "), s->msg)[0] = 0;
    s->api->say(s->ln);
    measure_sfl(s);
    s->stale = 1;
    moved(s);
    break;
  }
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
  if (!s->hold && (s->stale || s->now - s->t_full >= REFRESH_MS)) {   /* (monitor too) */
    int i;
    for (i = 0; i < NV && !s->qk; i++)
      if (bin_hz(s, i)) sweep_bin(s, i);
    if (!s->qk) sweep_done(s);
    s->tuned = 0;
    s->t_full = s->api->ms();
  }
}

/* hold (Jeff, 2026-11-24): no sweeps and no wake looks, the carets stay where they are; SURF
   listens on the middle with the squelch, or open with monitor too. The side keys still jump
   between the carets. The squelch here is the middle's own: the mean of the last 4 reads (~90 ms)
   against the middle's noise floor (the reads while shut, averaged), opening SQ_DB over it and
   shutting HANG_MS after it falls HYST_DB under that. Port 15 opened on any one read SQ_DB over
   the band's settled floor (its second lowest bin): with a read every ~20 ms, the noise crossed
   that often enough to open the speaker (Jeff: "Hold turns on the speaker") and keep it open. */
static void hold_step(sf_t *s) {
  int d, a, k;
  if (s->hnf == HNF_NONE) s->hnf = (s->sfl + 3) * 16;   /* (about the noise's middle; then measured) */
  if (!s->tuned) {
    d = read_at(s, C, CHECK_US);
    s->tuned = 1;
    hush(s, 0);
    for (k = 0; k < 4; k++) s->hr[k] = (int16_t)(s->hnf >> 4);
  } else {
    s->api->delay_us(PARK_US);
    d = dbm(s);
  }
  s->check = 0;
  s->sel_dbm = d;
  s->hr[s->hi++ & 3] = (int16_t)d;
  a = (s->hr[0] + s->hr[1] + s->hr[2] + s->hr[3]) * 4;   /* (the mean, 1/16 dB) */
  if (a >= s->hnf + (SQ_DB - (s->audio ? HYST_DB : 0)) * 16) s->t_open = s->now;
  else if (!s->audio) s->hnf += (d * 16 - s->hnf) / 16;
  s->parked = s->mon || s->now - s->t_open <= HANG_MS;
  if (s->parked != s->audio) audio(s, s->parked);
}

#if UV21_HIST
/* ---- the history strip (port 18; Jeff, 2026-11-24: "Let's go with history in RAM") ----
   Right of the bars, past a divider: one pixel column a finished sweep, the newest at the left
   edge and older ones pushed right, HN of them; each bin's 2 rows coloured by its level over the
   floor (HEAT: dark, blue, the bars' green, yellow, orange, white). The levels live in RAM, a
   nibble each (hring, 1,638 B), so the strip is redrawn whole from them, in one window, after each
   sweep. When the middle moves the stored columns are moved with it, each old bin to the new
   bin its frequency falls in, so a trail stays on its frequency; rows tuned into view show as
   none yet. A new span or band starts the strip over. Hold stops the sweeps, and so the strip. */
#define HUNK 15
static const uint16_t HEAT[16] = {     /* the floor's navy, blue, green, yellow, orange, white */
  0x0065, 0x00E9, 0x016D, 0x0231, 0x036F, 0x04AD, 0x05EB, 0x3E48,
  0x8645, 0xCE42, 0xFDE0, 0xFCE2, 0xFBE4, 0xFDB1, 0xFFFF,
  0x0841,                                 /* (none yet: near black) */
};
#define DIV RGB(40, 40, 40)

static int hget(sf_t *s, int n) { return n & 1 ? s->hring[n >> 1] >> 4 : s->hring[n >> 1] & 15; }
static void hset(sf_t *s, int n, int v) {
  uint8_t *b = &s->hring[n >> 1];
  *b = (uint8_t)(n & 1 ? (*b & 0x0F) | (v << 4) : (*b & 0xF0) | v);
}

/* the stored columns onto the scale on screen now */
static void hist_sync(sf_t *s) {
  int8_t map[NV];
  uint8_t a[NV], t[NV];
  int i, j, k;
  if (s->hbd == (const void *)s->bd && s->hsi == s->si && s->hcen == s->cen) return;
  if (s->hbd != (const void *)s->bd || s->hsi != s->si) {
    for (k = 0; k < (int)sizeof(s->hring); k++) s->hring[k] = 0xFF;   /* (a new scale: none yet) */
  } else {
    for (j = 0; j < NV; j++) {            /* old bin j's frequency, and the bin it's in now */
      uint32_t o = L_HZ * s->off[dist(j)], f = j < C ? s->hcen - o : s->hcen + o;
      int ok = j < C ? s->hcen >= o : f >= s->hcen;
      map[j] = (int8_t)(ok && f >= s->bd->lo && f <= s->bd->hi ? bin_of(s, f) : -1);
    }
    for (k = 0; k < HN; k++) {
      for (i = 0; i < NV; i++) a[i] = (uint8_t)hget(s, k * NV + i), t[i] = HUNK;
      for (j = 0; j < NV; j++) {
        i = map[j];
        if (a[j] != HUNK && i >= 0 && (t[i] == HUNK || a[j] > t[i])) t[i] = a[j];
      }
      for (i = 0; i < NV; i++) hset(s, k * NV + i, t[i]);
    }
  }
  s->hbd = (const void *)s->bd;
  s->hsi = s->si;
  s->hcen = s->cen;
  s->hdirty = 1;
}

/* bin i's level over the floor in 3 dB steps, 0-14 (HUNK none): its bar's colour and its
   history's (Jeff, 2026-11-24: "If we color the bar graph bars using heat map colors
   corresponding to signal strength then we can just copy that column across") */
static int heat_q(sf_t *s, int i) {
  int v = s->lvl[i], q;
  if (!bin_hz(s, i) || !v || !s->qfl) return HUNK;
  q = v > s->qfl ? (v - s->qfl) / 3 : 0;
  return q > 14 ? 14 : q;
}

/* a finished sweep: its bars' colours become the newest column */
static void hist_push(sf_t *s) {
  int i;
  s->hsw = s->sweeps;
  hist_sync(s);
  s->hhead = (uint8_t)(s->hhead + 1 == HN ? 0 : s->hhead + 1);
  for (i = 0; i < NV; i++) hset(s, s->hhead * NV + i, heat_q(s, i));
  s->hdirty = 1;
}

/* the strip, whole, in one window; the divider with it after a full redraw */
static void hist_draw(sf_t *s) {
  const uv_api_t *api = s->api;
  uint16_t row[HN];
  int i, c;
  hist_sync(s);
  if (s->sdirty) api->fill(BEND, BY, 1, 2 * NV, DIV);
  if (!s->hdirty && !s->sdirty) return;
  s->hdirty = 0;
  api->px_begin(HX, BY, HN, 2 * NV);
  for (i = NV - 1; i >= 0; i--) {
    for (c = 0; c < HN; c++) {
      int k = s->hhead - c;
      if (k < 0) k += HN;
      row[c] = HEAT[hget(s, k * NV + i)];
    }
    api->px_put(row, HN);
    api->px_put(row, HN);
  }
  api->px_end();
}
#endif

/* ---- the screen ---- */

/* bin i's bar length, 1..BW px: 5/4 px per quick dB over the window's floor */
static int bar_len(sf_t *s, int i) {
  int v = s->lvl[i], h = s->qfl && v > s->qfl ? ((v - s->qfl) * 5 * BW) / 400 : 0;   /* (100 px: 5/4 a dB) */
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

  /* typing a frequency (long #): TUNE in red on the title, the digits large with the cursor
     (Jeff, 2026-11-24), the level left out until it's done */
  if (s->ed == 3) {
    line(s, s->st0, "TUNE", 2, 0, 128, TITLE_H, RED, TBG, 0);
    p = put(t, s->eb[0]);
    *p++ = '_';
    *p = 0;
    line(s, s->st1, t, 2, FREQ_Y, 128, 21, FG, BG, 2);   /* (10x20, over the top bins) */
    line(s, s->st2, "*-POINT #-ENTER DN-DEL EXIT-QUIT", 0, MSG_Y, 128, 8, DIM, BG, 0);
    s->sdirty = 0;                              /* (the rest waits: ed_key repaints it all after) */
    return;
  }
  if (s->sdirty) api->fill(0, FREQ_Y, 128, BY - FREQ_Y, BG);   /* (TUNE's digits reach row 30) */

  /* the title: the band, its step, MON / HLD, a fixed gain, and the demodulator at the right */
  p = put(t, "SURF ");
  p = put(p, s->bd->name);
  *p++ = ' ';
  p = stepn(p, s->ch);
  if (s->mon) p = put(p, " MON");
  if (s->hold) p = put(p, " HLD");
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
  /* the bars, each redrawn only when its length, colour or caret changed, and then only the
     pixels that changed (port 17): the part a bar grew or shrank by, the old caret and the new */
  for (i = 0; i < NV; i++) {
    int y = BINY(i), len = bin_hz(s, i) ? bar_len(s, i) : 0, cx = 0;
#if UV21_HIST
    uint8_t col = (uint8_t)(heat_q(s, i) + 1);   /* (the heat colour + 1; HUNK + 1: none) */
    if (col == HUNK + 1) len = 0;                 /* (not read on this scale yet: no bar) */
#else
    uint8_t col = !bin_hz(s, i) ? 0 : i == C ? (s->audio ? 3 : 2) : 1;
#endif
    if (top[i]) {
      cx = 1 + (int)((uint32_t)top[i] * (BW - CW - 2) / HMAX);
      if (cx < len + 2) cx = len + 2;         /* (it rides on its bar, never inside it) */
      if (cx > BW - CW) cx = BW - CW;
    }
    if (!s->sdirty && s->sl[i] == len && s->scx[i] == cx && s->scol[i] == col) continue;
    {
#if UV21_HIST
      int ol = s->sl[i], ocx = s->scx[i], c = HEAT[(col - 1) & 15];
#else
      int ol = s->sl[i], ocx = s->scx[i], c = col == 3 ? LIVE : col == 2 ? MID : BAR;
#endif
      if (s->sdirty) {                      /* all of it */
        if (len) api->fill(BX, y, len, 2, (uint16_t)c);
        api->fill(BX + len, y, BEND - BX - len, 2, BG);
      } else {
        if (col != s->scol[i]) api->fill(BX, y, len, 2, (uint16_t)c);   /* (0 wide: nothing) */
        else if (len > ol) api->fill(BX + ol, y, len - ol, 2, (uint16_t)c);
        if (len < ol) api->fill(BX + len, y, ol - len, 2, BG);
        if (ocx && ocx != cx) {             /* the old caret, where the bar doesn't cover it */
          int x0 = ocx > len ? ocx : len;
          api->fill(BX + x0, y, ocx + CW - x0, 2, BG);
        }
      }
      if (cx) api->fill(BX + cx, y, CW, 2, FG);   /* (a caret never sits on its bar: always drawn) */
    }
    s->sl[i] = (uint8_t)len;
    s->scx[i] = (uint8_t)cx;
    s->scol[i] = col;
  }
#if UV21_HIST
  hist_draw(s);
#endif

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
    if (k) {
      on_key(s, k);
      s->now = api->ms();
      draw(s);
    }
    if (s->hold) hold_step(s);
    else if (s->check) check_bar(s);
    else if (s->parked) park_step(s);
    else sweep_step(s);
#if UV21_HIST
    if (s->sweeps != s->hsw) hist_push(s);
#endif
    s->now = api->ms();
    if (s->audio != s->saud || s->cen != s->dcen) {   /* the level is due once it settles */
      s->saud = s->audio;
      s->dcen = s->cen;
      s->dpend = 1;
      s->t_dq = s->now;
    }
    if (s->now - s->t_wake >= (s->parked || s->hold ? WAKE_PARK_MS : WAKE_MS)) wake_look(s);
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
