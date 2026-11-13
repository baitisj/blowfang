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
                     MHz, receive only); each starts at its centre (MAR/WX: on NOAA weather's
                     162.475, with the marine coast channels below) with its default step and span
     #               next channel step: 1, 5, 6.25, 8.33, 10, 12.5, 20, 25, 50, 100 kHz (the middle
                     stays; UP / DOWN then move onto the new raster; BAND resets the step)
     5               the band's centre (MAR/WX: 162.475)
     4               the demodulator: FM, DSB, AM, FM (DSB = the chip's "USB", both sidebands at
                     the zero IF; at the zero IF a steady carrier reads low, so DSB is for
                     listening); the title always shows it, at its far right; BAND resets it
     7 / 9           fixed receiver gain down / up: 0, -7, -12, -17, -22, -27, -33, -45 dB (from
                     auto, 9 starts at the top and 7 at -7); the title shows it ("70cm 25k G-12")
     8               the receiver's own AGC again (the gain leaves the title)
     1 / 3           zoom in / out: the span +-100 kHz, 250 kHz, 500 kHz, 1 MHz, 2.5 MHz, 5 MHz
     *               monitor: audio open on the bar all the time, no sweeping; the chip's own
                     squelch open all the way too (put back on leaving MON and at EXIT)
     0               hold: no refresh sweeps while a signal plays (default: one every 5 s)
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
#define BAR0 6                  /* first bar row: the middle's caret rows 1-3 */
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
#define NPM 128                 /* remembered peaks (8 B each, in the module store's free RAM) */
#define HMAX 152                /* a caret's height, x 1/8 px: 19 px above the axis's row 25 */
#define PUSH 64                 /* a sweep that hears the peak again lifts its caret 8 px */
#define NCONF 4                 /* settled reads a sweep may spend confirming new peaks */
#define NBAD 6                  /* an unconfirmed peak is read again after this many sweeps hear it */
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
#define AF_DSB 0x6540u          /* REG_47 AF 5, the firmwares' "USB": with REG_3D 0 (zero IF) it is DSB */
#define AF_MUTE 0x6040u

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

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
/* a caret's fall, ms per 1/8 px (A/B cycles it): the whole height in about 15 s, 1, 5, 20 min */
static const uint16_t FALL_MS[] = {100, 400, 2000, 8000};
static const char FALL[4][4] = {"15s", "1m", "5m", "20m"};
#define NFALL 4
#define MD_FM 0
#define MD_DSB 1
#define MD_AM 2
#define NMD 3
static const char MD_NAME[NMD][4] = {"FM", "DSB", "AM"};
/* the title's own glyphs (API level 9; Jeff 2026-11-12 picked them from mock-ups): 0x80 a surfer
   (in cell 0, from the screen's edge), 0x81 a speaker for MON, 0x82 a pause for HLD. Six columns
   each, from a pixel left of the character's cell; bit r is the title bar's row 1 + r */
static const uint16_t GLYPHS[3 * 6] = {
    0x108, 0x1C4, 0x0A4, 0x09F, 0x0E7, 0x048,
    0x038, 0x038, 0x07C, 0x1FF, 0x000, 0x0BA,
    0x000, 0x0FE, 0x0FE, 0x000, 0x0FE, 0x0FE};
/* the chip's squelch: glitch close (REG_4D [7:0]), glitch open (REG_4E [7:0]), noise close/open
   (REG_4F), RSSI open/close (REG_78); open all the way = egzumer's squelch 0: glitch 255, noise
   127, RSSI 0 */
static const uint8_t SQ_REGS[4] = {0x4D, 0x4E, 0x4F, 0x78};

/* a remembered peak: where (the loudest read's frequency), how wide the bin that heard it was
   (a coarse outer bin only says roughly where), its caret's height (0: free) and whether a
   settled read confirmed it (a quick-read noise bump stays BAD, unhoppable, until it falls) */
typedef struct {
  uint32_t f;
  uint16_t w;                   /* x L_HZ */
  uint8_t h, bad;
} pm_t;

typedef struct {
  const k6_api_t *api;
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

static int read_at(sf_t *s, int i, uint32_t us) { return read_hz(s, s->hz[i], us); }

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

/* the carets fall one 1/8 px every FALL_MS (time, not sweeps: a sweep takes 0.1 to 2.5 s) */
static void pm_fall(sf_t *s) {
  uint32_t n = udiv(s->now - s->t_fall, FALL_MS[s->fall]);
  int k;
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

static void on_key(sf_t *s, int k) {
  switch (k) {
  case 'U': go_to(s, s->cen + s->ch); break;
  case 'D': go_to(s, s->cen - s->ch); break;
  case '5':
    go_to(s, s->bd->home);
    break;
  case 'A': jump(s, 1); break;
  case 'B': jump(s, -1); break;
  case 'W':                           /* A/B: how fast the carets fall */
    s->fall = (uint8_t)(s->fall + 1 == NFALL ? 0 : s->fall + 1);
    put(put(s->msg, "fall "), FALL[s->fall])[0] = 0;
    s->t_msg = s->now + MSG_MS;
    break;
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
      if (s->hz[i]) sweep_bin(s, i);
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
  if (!s->hold && (s->stale || (!s->mon && s->now - s->t_full >= REFRESH_MS))) {   /* (stale: a zoom) */
    int i;
    for (i = 0; i < NV && !s->qk; i++)   /* (read_hz hushes the speaker; the next read unhushes it) */
      if (s->hz[i]) sweep_bin(s, i);
    if (!s->qk) sweep_done(s);          /* (a key cut it short: it goes first) */
    s->tuned = 0;
    s->t_full = s->api->ms();
  }
}

/* ---- the screen ---- */

static void px(uint8_t *b, int x, int r) {
  if ((unsigned)x < 128) b[16 * r + (x >> 3)] |= (uint8_t)(0x80 >> (x & 7));
}

static void xpx(uint8_t *b, int x, int r) {
  if ((unsigned)x < 128) b[16 * r + (x >> 3)] ^= (uint8_t)(0x80 >> (x & 7));
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
  if (s->mon || s->hold) {
    *p++ = ' ';
    *p++ = s->mon ? (x ? (char)0x81 : '*') : (x ? (char)0x82 : '=');
  }
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
    s->bd = &BANDS[2];
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
  for (;;) {
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
    if (k == 'X') break;
    if (k) {
      on_key(s, k);
      s->now = api->ms();
      draw(s);                        /* (at once: the press shows before the next read) */
    }
    if (s->check) check_bar(s);
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
