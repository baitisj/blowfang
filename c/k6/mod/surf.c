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
   lens's edges under their ticks). A dot in the top row marks a repeater the explorer saved (its
   table, c/k6/rt.h, read once at the start). Bins outside the band stay empty.

   Remembered peaks (Jeff, 2026-11-11: signals go away when people stop keying): every peak a
   sweep hears gets a caret "^" (4 px wide, 2 tall, XORed over the bars) that each sweep hearing
   it again pushes up 8 px, to 19 px over the floor at most, and that falls slowly the rest of
   the time (A/B: the whole height in about 1 minute at the start, then 5 min, 20 min, 15 s). A
   new peak first needs a settled read SQ_DB over the settled floor (at most NCONF a sweep): a
   quick-read noise bump gets no caret. A caret on the floor is forgotten. The side keys jump to
   the carets, not to what is on the air at the press, so a press never searches: the sweeps go
   on as they were. Up to NPM (128) are kept, 8 B each in the module store's free RAM (about
   4.9 KB is free after the code, so room is not the limit; 128 is two screens' worth of
   carets), for the band only (BAND forgets them; zoom and UP / DOWN keep them, off the screen
   too); with all in use a new one takes the lowest caret's place. The title line has a surfer, the band and its channel step, a fixed gain and the demodulator at
   the far right, line 1 the listening frequency and
   its level in dBm (a settled read), line 5 the axis's labels, or with the bar on a saved
   repeater its call, offset and TX tone ("N0CALL +5.0 103.5").

   Keys (the module scans the keypad itself: the module API has key_down but no key identity;
   the matrix is Aura's src/device/keypad.rs at edf3b98, rows PB0/PB4/PB10/PB11/PB9, columns
   PC13/PC14/PF6/PB14, side keys read with no row driven):
     UP / DOWN       the listening frequency one channel up / down (held: repeats); the scale
                     moves with it, each bin keeping the level of the old bin nearest it
     SIDE1 / SIDE2   (the orange side keys) jump to the nearest caret above / below (on screen or
                     off it), on the channel raster, whether or not it is on the air now; a caret
                     only a coarse outer bin heard is refined after the next read of the lens:
                     the loudest channel within that bin's width. With no caret that way, line 5 says
                     "no peak" and the middle stays
     A/B             how fast the carets fall: 1 min (the start), 5 min, 20 min, 15 s for the
                     whole height (line 5: "fall 5m")
     BAND            next band: 2m, 1.25m, 70cm, GMRS, marine + NOAA WX, airband (AM, 118-137
                     MHz, receive only), CUSTOM (the custom range: V or TUNE); each starts at its centre
                     (MAR/WX: on NOAA weather's 162.475, with the marine coast channels below)
                     with its default step and span
     V               TUNE (the UV-21's rules, Jeff 2026-11-24; a range until then): type a
                     frequency, shown large ('*' the point, DOWN deletes, # or MENU enters, EXIT
                     quits); CUSTOM is centred on it as wide as the band on screen (2-16 MHz).
                     Until then CUSTOM is SURF_USER_LO..HI, the UHF TV band
     #               next channel step: 1, 5, 6.25, 8.33, 10, 12.5, 20, 25, 50, 100 kHz, on release
                     (the middle stays; UP / DOWN then move onto the new raster; BAND resets the
                     step); held LONG_MS: TUNE, as V
     5               the band's centre (MAR/WX: 162.475)
     4               the demodulator: FM, DSB, AM, FM (DSB = the chip's "USB", both sidebands at
                     the zero IF; at the zero IF a steady carrier reads low, so DSB is for
                     listening); the title always shows it, at its far right; BAND resets it
     7 / 9           fixed receiver gain down / up: 0, -7, -12, -17, -22, -27, -33, -45 dB (from
                     auto, 9 starts at the top and 7 at -7); the title shows it ("70cm 25k G-12")
     8               the receiver's own AGC again (the gain leaves the title)
     1 / 3           zoom in / out: the span +-100 kHz, 250 kHz, 500 kHz, 1 MHz, 2.5 MHz, 5 MHz,
                     10 MHz, 25 MHz (the last two: outer bins read 24 times, not every 20 kHz)
     *               monitor: audio open on the bar all the time and the chip's own squelch open
                     all the way (put back on leaving MON and at EXIT); the refresh sweeps (one
                     every 5 s) and the carets' fall go on as ever (Jeff, 2026-11-24)
     0               hold: no sweeps at all and the carets stop falling; the bar listens with the
                     squelch as usual (open too with monitor) and the side keys still jump between
                     the carets. Monitor and hold are independent (Jeff, 2026-11-24)
     MENU            say the bar's frequency and level on the serial port
     EXIT            leave (the module waits for the key's release, so the pager doesn't see it)

   One receiver both sweeps and listens, so the module listens first: while the bar's channel
   is quiet it sweeps (audio muted), and every CHK_MS it goes back to the bar for a settled
   read; a signal there SQ_DB over the settled floor opens the audio (REG_47 FM, speaker amp on
   PB2, as dmrdet) and parks on it. It closes HANG_MS after the signal drops, and then sweeps
   again. While parked, a refresh sweep every REFRESH_MS (about 0.3 s of muted audio) keeps the
   bars current, unless hold (0) or monitor is on. A move onto a signal reads only the lens
   before the audio opens; the rest waits for that refresh. Any key cuts a sweep short and the
   screen redraws at once (Jeff, 2026-11-11: jumps and UP / DOWN were slow). Any retune with the audio open turns the
   speaker amp off as well until the bar has settled again (hush): REG_47 muted alone buzzed.

   Levels: sweep reads are quick (SETTLE_US after the tune, as Aura's own spectrum: about 59 %
   of the way in dB from the -173 dBm restart, Phase 0), so the bars are relative: the floor is
   the quartile of the window's bins, each pixel of height about 1.1 quick dB. Bars fall at
   most DECAY_DB a sweep and rise at once. The squelch and line 1 use settled reads (CHECK_US):
   the settled floor is the second lowest of 8 bins' settled reads, measured when the window
   changes and every 30 s of sweeping, and a quieter read of the bar lowers it. The receiver's
   AGC is left alone (dmrdet's frozen gain shifts RSSI by up to 15 dB).

   surfauto.c builds the same module for unattended bench runs: SIDE1 presses itself every
   SURF_AUTO_MS (each caret it lands on says "sel" with a settled read there and the settled
   floor), with no caret above it wraps to the band's bottom, and it leaves after SURF_AUTO_S;
   keys still work. Before it ends it says its last bargraph ("bar 441.2000 1000k lens": the middle and the span, then two lines of a letter a
   bin, tools/k6_screen.py draws them).

   Lines (K6 MOD say ...): "surf 70cm 440.850 25k", "sel 441.2000 -71 -127" (MENU, or the
   bench build's jumps: the bar, its settled dBm, the settled floor), "end sw=N".
   Writes REG_47 and GPIOB bit 2 (speaker), REG_73 bit 4 (the AFC, off on airband), REG_13 and
   REG_7E (fixed gain) and the keypad rows' GPIOB BSRR/BRR bits, all put back.
   Reads the SPI flash (level 5's fl_read), never writes it. No TX. The state (with the bitmap and
   a move's copy of the old scale, ~1.7 KB) and the remembered peaks live in the module store's
   free RAM after the code; the stack holds only calls and locals (tests/test_k6_surf.py measures
   it: it must leave room for Aura's own calls under the module's, which the emulator doesn't
   count: with ~2.1 KB measured, the 2,280 B the K6 gives a module ran out on the radio).
   Returns the sweeps done, -3 (an image below level 6) or -4 (too little free RAM for the state).

   The radio-independent half (the scale, the sweep of one bin, the floor, the carets and the
   side-key jumps) lives in c/common/surf_core.inc, shared with the RT-950 Pro's two-chip SURF
   (radios/rt950/src/surf2.c, 2026-11-16). This file keeps what is the K6's: its one receiver's
   reads, the listen-first state machine with hush, the keypad, the screen and the explorer's
   repeater table. The move left the module byte for byte the same.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 6
#define K6_MODULE_OPT z
#endif
#include "../k6_mod.h"
#ifndef K6_NAME
#define K6_NAME "SURF"   /* its name in the MENU list (a file that includes this one may set its own) */
#endif
K6_MODULE_NAME(K6_NAME);

#define BAR0 6                  /* first bar row: the middle's caret rows 1-3 */
#define AX 27                   /* the axis: its line, then ticks in rows 28 (all) and 29 (major) */
#define BARH (AX - BAR0)
#define CHK_MS 70               /* sweeping: a check of the bar's channel this often */
#define PARK_US 20000           /* a parked read every ~20 ms */
#define REFRESH_MS 5000
#define FLUSH_MS 200            /* the screen (each flush ~20-40 ms) */
#define LONG_MS 600             /* a long press of # (TUNE) */
#if !defined SURF_AUTO_S && !defined SURF_NO_TUNE
#define SURF_TUNE               /* (the unattended bench build, surfauto.c, has no TUNE: no room) */
#endif
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

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

/* the custom range (Jeff, 2026-11-17): BAND's last stop, CUSTOM (USER until 2026-11-24), is a band the keypad sets (V);
   until then it is SURF_USER_LO .. SURF_USER_HI, the UHF TV band, whose pilots the drift
   calibration wants (docs/k6-surf.md "Custom range") */
#ifndef SURF_USER_LO
#define SURF_USER_LO 470000000u
#endif
#ifndef SURF_USER_HI
#define SURF_USER_HI 608000000u
#endif
#define USER_MIN 18000000u      /* what the K6 receives (docs/k6-rxmap.md: 18-630 MHz) */
#define USER_MAX 630000000u
#define ED_LEN 7                /* a typed frequency: "470.309" */
#define SF_EXTRA                                                                                   \
  band_t ub;                    /* CUSTOM */                                                       \
  uint8_t ed, en[2];            /* typing: 1 / 2 V's low / high end, 3 TUNE, 0 none; lengths */    \
  char eb[2][ED_LEN + 1];       /* the typed text */                                             \
  uint32_t t_hash;              /* when # went down (a long press: TUNE) */                        \
  uint8_t hpend;                /* # is down: a step on release, TUNE if held LONG_MS */
#define SF_CUSTOM               /* the core's TUNE, typed range and custom range */

#include "../../common/surf_core.inc"   /* the scale, the sweep of a bin, the floor, the carets */

/* airband is receive only (the K6 never transmits there) and sits below its 136-174 MHz filter.
   Home is the band's centre, but MAR/WX's: NOAA weather's 162.475 (Jeff 2026-11-12: its centre,
   159.275 at +-1 MHz, showed neither the weather channels nor most of marine) */
static const band_t BANDS[] = {
    {144000000u, 148000000u, 146000000u, ST_10K, 2, 0, "2m"},
    {222000000u, 225000000u, 223500000u, ST_10K, 2, 0, "1.25m"},
    {430000000u, 450000000u, 440000000u, ST_25K, 3, 0, "70cm"},
    {462550000u, 467725000u, 465137500u, ST_12K5, 4, 0, "GMRS"},
    {156000000u, 162550000u, 162475000u, ST_25K, 3, 0, "MAR/WX"},
    {118000000u, 137000000u, 127500000u, ST_25K, 3, 1, "AIR"}};
#define NBANDS 6

/* Aura's KEY_TABLE: phase 0 (no row driven) side keys, then rows PB0, PB4, PB10, PB11, PB9;
   A/B = side 1/2, U/D up/down, M menu, X exit, N band, V vm, W a/b */
static const char KEYS[24] = {0,   'A', 0,   'B', '7', '8', '#', '9', '4', '5', '0', '6',
                              '1', '2', '*', '3', 'M', 'U', 'X', 'D', 'V', 'W', 0,   'N'};
static const uint8_t ROWPIN[6] = {0xFF, 0, 4, 10, 11, 9};
/* the title's own glyphs (API level 9; Jeff 2026-11-12 picked them from mock-ups): 0x80 a surfer
   (in cell 0, from the screen's edge), 0x81 a speaker for MON, 0x82 a pause for HLD. Six columns
   each, from a pixel left of the character's cell; bit r is the title bar's row 1 + r */
static const uint16_t GLYPHS[3 * 6] = {
    0x108, 0x1C4, 0x0A4, 0x09F, 0x0E7, 0x048,
    0x038, 0x038, 0x07C, 0x1FF, 0x000, 0x0BA,
    0x000, 0x0FE, 0x0FE, 0x000, 0x0FE, 0x0FE};

static int dbm(sf_t *s) { return KM_RSSI_DBM(s->api->fd_read(0x67)); }

/* with the audio open: speaker off (1) while the receiver tunes about, back on (0) once it has
   settled on the listening frequency again; a retune with the amp on buzzes (Jeff, 2026-11-10) */
static void hush(sf_t *s, int on) {
  if (!s->audio || on == s->hush) return;
  s->hush = (uint8_t)on;
  s->api->fd_write(0x47, on ? AF_MUTE : s->af);
  wr(GPIOB + (on ? BRR : BSRR), SPK);
}

static void kp_peek(sf_t *s);

/* the level at hz in dBm, us after its tune (the wide filter); a look at the keys too, so a
   press during a whole sweep (up to ~0.5 s at the wide spans) isn't lost */
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
  wr(GPIOB + (on ? BSRR : BRR), SPK);
}

/* the demodulator (the band's, or key 4's): AM (REG_47 AF 7) with the AFC off, as Aura's
   apply_modulation; DSB (AF 5 at the zero IF, REG_3D 0, the AFC off: the firmwares' "USB",
   docs/k6-afsweep.md); else FM with REG_73 as Aura had it. Aura's AM AGC (REG_14 0, REG_49
   (50 << 7) | 32) is left out: with it the RSSI never rose on the IC-7100's AM carrier
   (docs/k6-if-modem.md, 2026-11-06), and the bars and the squelch are all RSSI. REG_3D is
   written only once DSB has been used (the tuning keeps it: docs/k6-acars.md, seventh run) */
static void demod(sf_t *s) {
  int md = s->md;
  const k6_api_t *api = s->api;
  s->af = (uint16_t)(md == MD_AM ? AF_AM : md == MD_DSB ? AF_DSB : AF_FM);
  if (md == MD_DSB || s->zif) {
    s->zif = 1;
    api->fd_write(0x3D, (uint16_t)(md == MD_DSB ? 0 : s->r3d));
  }
  api->fd_write(0x73, (uint16_t)((s->r73 & ~0x10u) | (md != MD_FM ? 0x10u : 0)));
  if (s->audio && !s->hush) api->fd_write(0x47, s->af);
}

/* monitor: the chip's own squelch open all the way (on), or put back as Aura had it. Jeff heard
   MON cut weak signals (2026-11-11) though the module never closes the audio there: the chip
   (or Aura under it) still follows its squelch result, REG_0C bit 1 */
static void squelch(sf_t *s, int on) {
  const k6_api_t *api = s->api;
  int k;
  if (on)
    for (k = 0; k < 4; k++) s->sq[k] = api->fd_read(SQ_REGS[k]);
  api->fd_write(0x4D, on ? (uint16_t)((s->sq[0] & 0xFF00u) | 0xFFu) : s->sq[0]);
  api->fd_write(0x4E, on ? (uint16_t)((s->sq[1] & 0xFF00u) | 0xFFu) : s->sq[1]);
  api->fd_write(0x4F, on ? 0x7F7Fu : s->sq[2]);
  api->fd_write(0x78, on ? 0x0000u : s->sq[3]);
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

/* ---- the custom range (CUSTOM) ---- */

/* CUSTOM on lo .. hi: the middle on its centre, the smallest span that shows it all (or the
   widest), 25 kHz steps (# changes it), AM if it is all airband */
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
  put(u->name, "CUSTOM")[0] = 0;
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

/* between reads: a new key read the same twice in a row is kept for the main loop (once), and a
   release read twice lets the same key count again */
static void kp_peek(sf_t *s) {
  int k = kp_scan(s);
  if (k == s->k1) {
    if (!k) {
      s->key = 0;
    } else if (k != s->key && !s->qk) {
      s->qk = s->key = (uint8_t)k;
      s->t_rep = s->now + 400;
    }
  }
  s->k1 = (uint8_t)k;
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

/* ---- listening and sweeping with one receiver ---- */

/* after the bar moves: listen there afresh (monitor: just retune) */
static void moved(sf_t *s) {
  s->tuned = 0;
  if (!s->mon) {
    audio(s, 0);
    s->parked = 0;
    s->check = 1;
  }
}

static void on_key(sf_t *s, int k) {
  if (s->ed) {
    ed_key(s, k);
    return;
  }
  switch (k) {
  case 'V':                          /* TUNE (the UV-21's rules, Jeff 2026-11-24) */
#ifdef SURF_TUNE
  case 'T':                          /* (a long #, as on the UV-21) */
#endif
    ed_start(s, 3);
    break;
  case 'U': go_to(s, s->cen + s->ch); break;
  case 'D': go_to(s, s->cen - s->ch); break;
  case '5':
    go_to(s, s->bd->home);
    break;
  case 'A': jump(s, 1); break;
  case 'B': jump(s, -1); break;
  case 'W':                           /* A/B: how fast the carets fall */
    /* Not adopted yet (Jeff, 2026-11-24): flux_radio's SURF (tcarwash/flux_radio e34869d, MIT)
       makes a held A/B (600 ms, SURF_AB_HOLD_MS) on a signal that plays tune VFO B to the middle
       (api->vfo_b_tune(cen, mode)) and leave SURF with B active; a short press stays fall speed.
       It needs a vfo_b_tune entry in the module table, which this firmware doesn't have. */
    s->fall = (uint8_t)(s->fall + 1 == NFALL ? 0 : s->fall + 1);
    put(put(s->msg, "fall "), FALL[s->fall])[0] = 0;
    s->t_msg = s->now + MSG_MS;
    break;
  case 'N':                           /* the next band; CUSTOM after the last, then the first */
    band(s, s->bd == &s->ub ? BANDS : s->bd + 1 == BANDS + NBANDS ? &s->ub : s->bd + 1);
    break;
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
    break;
  case '*':
    s->mon ^= 1;
    squelch(s, s->mon);
    s->tuned = 0;
    s->parked = s->mon;               /* (monitor: park_step opens the audio once tuned) */
    if (!s->mon) moved(s);
    break;
  case '0':
    s->hold ^= 1;
    if (!s->hold) moved(s);           /* (sweeping again: check the middle first) */
    break;
  case '4':                           /* the demodulator: FM, DSB, AM, FM (a new band resets it) */
    s->md = (uint8_t)(s->md + 1 == NMD ? 0 : s->md + 1);
    demod(s);
    note(s, MD_NAME[s->md]);
    measure_sfl(s);                   /* (the zero IF's floor is another) */
    s->stale = 1;
    moved(s);
    break;
  case 'M': say_sel(s); break;
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
  /* a signal there, or a jump to refine (it may have landed a channel off its station): the lens
     first (17 single reads, ~0.1 s), for the refine and the station's shape. Parking, the rest
     keeps the levels the old scale lent it until the first refresh (a whole sweep here held the
     audio and the keys 1-2 s: Jeff, 2026-11-11); sweeping, the next sweep replaces them all */
  if (s->stale && (d >= s->sfl + SQ_DB || s->refine)) {
    for (i = C - KI; i <= C + KI && !s->qk; i++)
      if (bin_hz(s, i)) sweep_bin(s, i);
    if (s->qk) return;                  /* a key came: it goes first */
    if (s->refine && refine_lens(s)) return;   /* the middle moved again: check there */
    s->tuned = 0;
  }
  if (d >= s->sfl + SQ_DB) {
    s->stale = 0;
    s->parked = 1;
    s->t_open = s->t_full = s->api->ms();
    /* no whole sweep yet on this window (BAND, the start): one now, not 5 s on, so the band's
       other stations get their carets while this one plays (Jeff, 2026-11-12) */
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
  /* monitor: open here, not only on the retune above: a bar check due when '*' came (one every
     CHK_MS while sweeping) did the retune, and the speaker stayed shut (Jeff, 2026-11-13) */
  if (s->mon && !s->audio) audio(s, 1);
  s->sel_dbm = d;
  if (d >= s->sfl + SQ_DB) pm_heard(s);   /* (monitor too: what plays gets a caret) */
  if (d >= s->sfl + SQ_DB - HYST_DB) s->t_open = s->now;
  if (!s->mon && s->now - s->t_open > HANG_MS) {
    audio(s, 0);
    s->parked = 0;
    return;
  }
  if (!s->hold && (s->stale || s->now - s->t_full >= REFRESH_MS)) {   /* (stale: a zoom; monitor
                                                                      too: Jeff, 2026-11-24) */
    int i;
    for (i = 0; i < NV && !s->qk; i++)   /* (read_hz hushes the speaker; the next read unhushes it) */
      if (bin_hz(s, i)) sweep_bin(s, i);
    if (!s->qk) sweep_done(s);          /* (a key cut it short: it goes first) */
    s->tuned = 0;
    s->t_full = s->api->ms();
  }
}

/* hold (Jeff, 2026-11-24): no sweeps at all and the carets stay where they are; SURF listens on
   the middle with the squelch as usual (a signal over the floor opens the audio, HANG_MS after
   it the audio shuts), or open with monitor too. The side keys still jump between the carets. */
static void hold_step(sf_t *s) {
  int d;
  if (!s->tuned) {
    d = read_at(s, C, CHECK_US);
    s->tuned = 1;
    hush(s, 0);
  } else {
    s->api->delay_us(PARK_US);
    d = dbm(s);
  }
  s->check = 0;
  s->sel_dbm = d;
  if (d >= s->sfl + SQ_DB - (s->audio ? HYST_DB : 0)) s->t_open = s->now;
  s->parked = s->mon || s->now - s->t_open <= HANG_MS;
  if (s->parked != s->audio) audio(s, s->parked);
}

/* ---- the screen ---- */

static void px(uint8_t *b, int x, int r) {
  if ((unsigned)x < 128) b[16 * r + (x >> 3)] |= (uint8_t)(0x80 >> (x & 7));
}

static void xpx(uint8_t *b, int x, int r) {
  if ((unsigned)x < 128) b[16 * r + (x >> 3)] ^= (uint8_t)(0x80 >> (x & 7));
}

/* TUNE's typing in large digits (the UV-21's 10x20 on this screen, Jeff 2026-11-24): a 5x7 font
   at twice its size, 10x14 px on a 12 px pitch, rows TUNE_R.. of the bitmap; "0"-"9", "." and "_"
   (the cursor), a row a byte, bit 4 leftmost */
#define TUNE_R 8
static const uint8_t BIG[12][7] = {
    {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E}, {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E},
    {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F}, {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E},
    {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02}, {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E},
    {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E}, {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08},
    {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E}, {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C},
    {0, 0, 0, 0, 0, 0x0C, 0x0C},                {0, 0, 0, 0, 0, 0, 0x1F}};

static void big(uint8_t *b, const char *t) {
  int x = 4, r, c;
  for (; *t; t++, x += 12) {
    const uint8_t *g = BIG[*t == '.' ? 10 : *t == '_' ? 11 : *t - '0'];
    for (r = 0; r < 14; r++)
      for (c = 0; c < 10; c++)
        if (g[r >> 1] & (0x10 >> (c >> 1))) px(b, x + c, TUNE_R + r);
  }
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
  if (s->ed) {                            /* TUNE: the title, the typing large, the keys' hint */
    p = put(s->ln, s->eb[0]);
    put(p, "_")[0] = 0;
    big(b, s->ln);
    api->lcd_text(0, "TUNE");
    api->lcd_text(1, "*-POINT  #-ENTER");
    api->lcd_text(5, (int32_t)(s->t_msg - s->now) > 0 ? s->msg : "DN-DEL  EXIT-QUIT");
    api->lcd_wf(b, ROWS, 0);
    api->lcd_flush();
    return;
  }
  for (i = 0; i < NV; i++) {
    if (!bin_hz(s, i)) continue;
    for (r = AX - bar_h(s, i); r < AX; r++) pair(b, i, r);
    pair(b, i, AX);                       /* the axis line */
  }
  {
    /* the remembered peaks' carets, XOR over the bars: a bin's highest one, its tip (2 px) in row
       AX - 2 - h / 8 and its feet (2 more px) under it, "^"; one that would sit inside its own
       bar rides on top of it instead (inside a tall bar it was a notch Jeff couldn't see) */
    uint8_t *top = s->tmp;
    for (i = 0; i < NV; i++) top[i] = 0;
    for (k = 0; k < s->npm; k++) {
      pm_t *e = &s->pm[k];
      int j;
      if (!e->h || e->bad || (j = bin_of(s, e->f)) < 0) continue;
      if (e->h > top[j]) top[j] = e->h;
    }
    for (i = 0; i < NV; i++) {
      if (!top[i]) continue;
      r = AX - 2 - (top[i] >> 3);
      if (r + 1 >= AX - bar_h(s, i)) r = AX - bar_h(s, i) - 2;
      if (r < BAR0 - 2) r = BAR0 - 2;
      xpx(b, 2 * i, r), xpx(b, 2 * i + 1, r);
      xpx(b, 2 * i - 1, r + 1), xpx(b, 2 * i + 2, r + 1);
    }
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
    int j, e, big = TICKS[i] == 20 || TICKS[i] == 100 || TICKS[i] == 1000 || TICKS[i] == 10000;
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

  /* the title (Jeff 2026-11-12): the surfer, the band and its step, MON or HLD, a fixed gain,
     and the demodulator always at the far right ("<surfer>GMRS 12.5k <spk> G-12     DSB", 21
     columns). The glyphs need API level 9; before it '~' is the surfer, '*' MON (its key) and
     '=' HLD. Short of room the gain drops its G, then goes (line 5 said it when it was set) */
  x = api->level >= 9;
  if (x) api->lcd_glyphs(GLYPHS, 3);
  p = s->ln;
  *p++ = x ? (char)0x80 : '~';
  p = put(p, s->bd->name);
  *p++ = ' ';
  p = stepn(p, s->ch);
  if (s->mon || s->hold) *p++ = ' ';
  if (s->mon) *p++ = x ? (char)0x81 : '*';
  if (s->hold) *p++ = x ? (char)0x82 : '=';
  if (s->gx) {
    char t[5], *e = sint(t, GAIN_DB[s->gx - 1]);
    int n = e - t, room = 21 - 4 - (p - s->ln);       /* 4: a space and the longest mode */
    if (room >= n + 2) *p++ = ' ', *p++ = 'G';
    else if (room > n) *p++ = ' ';
    else n = 0;
    for (r = 0; r < n; r++) *p++ = t[r];
  }
  for (r = 0; MD_NAME[s->md][r]; r++) ;
  while (p < s->ln + 21 - r) *p++ = ' ';
  p = put(p, MD_NAME[s->md]);
  *p = 0;
  api->lcd_text(0, s->ln);
  p = mhz(s->ln, s->cen, cdec(s));
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
  sf_t *s;
  uint32_t n, room;
  uint8_t *z;
  unsigned i;
  int k;
  char *p;
  if (api->level < 6) return -3;
  /* the state, the bitmap and the remembered peaks in the store's free RAM: on the stack (with
     Aura's own calls under it) the 2,280 B a module has ran out, and the K6 rebooted at the end
     (stack_free=0 B, Jeff's run 2026-11-11 08:08Z) */
  z = (uint8_t *)api->scratch(&room);
  if (room < sizeof(sf_t)) return -4;
  for (i = 0; i < sizeof(sf_t); i++) z[i] = 0;
  s = (sf_t *)z;
  room -= sizeof(sf_t);
  s->api = api;
  s->bmp = s->bm;
  s->fall = 1;                          /* carets fall in about a minute */
  s->pm = (pm_t *)(z + sizeof(sf_t));
  s->npm = room >> 3 < NPM ? (int)(room >> 3) : NPM;
  for (n = 0; n < (uint32_t)s->npm; n++) s->pm[n].h = 0;
  s->r47 = api->fd_read(0x47);
  s->r13 = api->fd_read(0x13);
  s->r73 = api->fd_read(0x73);
  s->r7e = api->fd_read(0x7E);
  s->r3d = api->fd_read(0x3D);
  s->odr = rd(GPIOB + ODR) & SPK;
  s->af = AF_FM;
  s->audio = 1;
  audio(s, 0);
  {
    uint32_t hz = api->rx_hz();
    user_set(s, SURF_USER_LO, SURF_USER_HI);
    s->bd = hz >= s->ub.lo && hz <= s->ub.hi ? &s->ub : &BANDS[2];
    for (i = 0; i < NBANDS; i++)
      if (hz >= BANDS[i].lo && hz <= BANDS[i].hi) s->bd = &BANDS[i];
    s->st = s->bd->st;
    s->si = s->bd->si;
    s->md = s->bd->am ? MD_AM : MD_FM;
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
  while (!km_power_switch_off()) {   /* (the knob turned off: leave, so Aura powers down; flux_radio c556d5c) */
    s->now = api->ms();
    pm_fall(s);
    k = kp_event(s);
    if (s->qk) k = s->qk, s->qk = 0;   /* (one caught during a sweep comes first) */
#ifdef SURF_AUTO_S
    if (!k && !s->check && s->now - s->t_auto >= SURF_AUTO_MS) {
      k = 'A';
      s->t_auto = s->now;
    }
    if (s->now >= SURF_AUTO_S * 1000u) break;
#endif
#ifdef SURF_SCRIPT
    if (!k && s->now - s->t_auto >= SURF_SCRIPT_MS) {
      static const char SC[] = SURF_SCRIPT;
      s->t_auto = s->now;
      if (s->sc) {                    /* what the last key left */
        p = put(s->ln, "k");
        *p++ = SC[s->sc - 1];
        *p++ = ' ';
        p = mhz(p, s->cen, cdec(s));
        *p++ = ' ';
        p = sint(p, s->sel_dbm);
        *p++ = ' ';
        p = sint(p, s->sfl);
        p = put(p, " g");
        p = sint(p, s->gx);
        *p++ = ' ';
        p = put(p, s->bd->name);
        *p++ = ' ';
        say_end(s, stepn(p, s->ch));
      }
      if (!SC[s->sc]) break;
      k = SC[s->sc++];
    }
#endif
#ifdef SURF_TUNE
    /* # waits for its release: a short press is the next step, one held LONG_MS is TUNE (the
       UV-21's long #, Jeff 2026-11-24). Typing, # is the typing's own at once */
    if (s->hpend) {
      if (s->key != '#') {
        s->hpend = 0;
        if (k) s->qk = (uint8_t)k;       /* (a key pressed as # came up: next time round) */
        on_key(s, '#');
        s->now = api->ms();
        draw(s);
        k = 0;
      } else if (s->now - s->t_hash >= LONG_MS) {
        s->hpend = 0;
        k = 'T';
      }
    } else if (k == '#' && !s->ed) {
      s->hpend = 1;
      s->t_hash = s->now;
      k = 0;
    }
#endif
    if (k == 'X' && !s->ed) break;   /* (typing, EXIT drops the typing) */
    if (k) {
      on_key(s, k);
      s->now = api->ms();
      draw(s);                        /* (at once: the press shows before the next read) */
    }
    if (s->hold) hold_step(s);
    else if (s->check) check_bar(s);
    else if (s->parked) park_step(s);
    else sweep_step(s);
    s->now = api->ms();
    if (s->now - s->t_flush >= FLUSH_MS) draw(s);
  }
  /* put back the audio path, the demodulator and the gain; wait (up to 3 s) for the keys' release */
  api->fd_write(0x47, (uint16_t)s->r47);
  api->fd_write(0x13, (uint16_t)s->r13);
  api->fd_write(0x73, (uint16_t)s->r73);
  api->fd_write(0x7E, (uint16_t)s->r7e);
  if (s->zif) api->fd_write(0x3D, (uint16_t)s->r3d);
  if (s->mon) squelch(s, 0);
  wr(GPIOB + (s->odr ? BSRR : BRR), SPK);
  for (i = 0, n = 0; i < 300 && n < 5 && !km_power_switch_off(); i++) {
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
    *p++ = bin_hz(s, k) ? (char)('a' - 1 + bar_h(s, k)) : '-';
    if (!(++k & 31) || k == NV) say_end(s, p), p = s->ln;
  }
#endif
  p = put(s->ln, "end sw=");
  say_end(s, fix(p, (uint32_t)s->sweeps, 0));
  return s->sweeps;
}
