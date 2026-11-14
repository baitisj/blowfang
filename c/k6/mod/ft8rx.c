/* ft8rx: an FT8 receiver on the K6, from the receive IF (docs/k6-ft8-wspr.md).

   The IC-7100 (or anyone) sends FT8 in USB on the pager's frequency; the K6 stays in its FM
   receive set-up, so a USB tone at audio f sits on the FD6818's low IF at 26 MHz / 1536 + f
   (or - f: FT8_INV). GP0 code 13 puts the IF on PB1 as a 1-bit sigma-delta (PR #68), DMA
   channel 1 reads it flat out into a ring (gp0dma, PR #69), and this module counts 64 samples
   at a time and hands each count to c/k6/ft8/ft8k.c with the mixer phase for the moment it
   was taken. The phase comes from real time (TIM17 at 96 MHz, kept unwrapped with Aura's
   100 us TIM14), not from counting samples, because the DMA's rate moves with bus load and
   stops while a row is decoded.

   ft8k keeps a 162-row waterfall of FT8_NB bins around FT8_AUDIO Hz (24 bins: 150 Hz, enough
   for the K6's offset from the IC-7100 of tens of Hz) and decodes as it goes; there is no UTC
   clock, so transmissions can start whenever they like. Before it starts, the module sets
   the front end's gain so the receiver noise fills the 1-bit path well (the simulation: the
   pager's AGC level costs 2-4 dB, 10 dB more gain about none): it measures the counts' rms at
   the AGC, and if that is under FT8_RMS_LO it fixes the AGC (REG_7E bit 15, index 3) and
   takes the highest REG_13 gain whose noise is under FT8_RMS_HI.

   Lines:
     ft8 start cal=-16580 rms=4.7 g=03ff rms=9.1 r2=27 ks=5490  (the IF offset the mixer
                                                  follows, 0.01 Hz, 0 for none; noise rms; DMA kS/s)
     ft8 CQ AG7EW CN87 f=1503 s=-12 t=12.4                 (tone 0 Hz, SNR dB, start s)
     ft8 st t=15 rows=375 lost=2 ks=5492 dec=1 idle=88     (every 15 s)
   Runs FT8_SECONDS (300) or until a key, and returns the decodes; or -2 (a key before the
   start), -3 (old image), -4 (PB1 not an input), -5 (REG_33 refused), -7 (TIM17 or DMA channel
   1 in use), -8 (scratch too small). Writes REG_34[3:0], REG_33 bit 8, REG_73 bit 4 (AFC off),
   REG_7E and REG_13 (the gain), TIM17 and DMA channel 1 with their clocks, all put back.
   Receive only.

   The screen (Jeff 2026-11-14: "tune to the appropriate frequencies and then start receiving and
   decoding"; an image with the module screen, level 6+, only): first a band list, the FT8 dial
   frequencies the K6 can hear, 6 m 50.313, 2 m 144.174, 70 cm 432.174, and HERE (the pager's own
   frequency, the bench's 440.850). UP / DOWN and MENU (or the band's digit 1-4) pick one; with no
   key it starts on 2 m after FT8_PICK_S seconds; EXIT leaves. Then it tunes there (USB dial:
   the receiver's IF stays the FM set-up's, as on the bench) and listens until EXIT, with no time
   limit:

     FT8 2M 144.174            title: band and dial
     CQ AG7EW CN87 -12         the newest decode and its SNR (or what it listens to)
     (waterfall)               the window's 24 bins over about 15 s, one row a ~0.5 s
     1:23 1500 D3 SCAN         time, the window's centre (audio Hz), decodes, SCAN on

   The window is 150 Hz of the 2.5 kHz an SSB receiver hears (ft8k's 24 bins of 6.25 Hz; the
   CPU and RAM hold no more). UP / DOWN move it 150 Hz (600 to 2700 Hz) and stop SCAN; 5 turns
   SCAN on and off: it moves the window up one step every FT8_HOP_S seconds (30: every window
   gets one whole 15 s FT8 slot even with no UTC clock), round and round. SCAN starts on.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "../ft8/ft8k.c"
#ifndef K6_NAME
#define K6_NAME "FT8 RX"   /* its name in the MENU list (a file that includes this one may set its own) */
#endif
K6_MODULE_NAME(K6_NAME);

#ifndef FT8_NB
#define FT8_NB 24
#endif
#ifndef FT8_AUDIO
#define FT8_AUDIO 1500 /* Hz: the window's centre, as a USB dial offset */
#endif
#ifndef FT8_INV
#define FT8_INV 0      /* 1: the IF is mirrored (the tone at IF - f) */
#endif
#ifndef FT8_SECONDS
#define FT8_SECONDS 300
#endif
#ifndef FT8_STAT
#define FT8_STAT 375 /* rows between status lines: 15 s */
#endif
#ifndef FT8_RMS_LO
#define FT8_RMS_LO 80  /* counts' rms x 10 under which the gain goes up */
#endif
#ifndef FT8_RMS_HI
#define FT8_RMS_HI 140
#endif

#ifndef FT8_PICK_S
#define FT8_PICK_S 10  /* the band list's wait before it starts on 2 m */
#endif
#ifndef FT8_HOP_S
#define FT8_HOP_S 30   /* SCAN: seconds in each window */
#endif
#ifndef FT8_UI_ROWS
#define FT8_UI_ROWS 0xFFFFFFFFu /* with the screen: until EXIT (the tests set a limit) */
#endif
#define FT8_STEP 150   /* Hz: the window's step (FT8_NB bins of 6.25 Hz) */
#define FT8_WLO (-6)   /* steps from FT8_AUDIO: 600 Hz */
#define FT8_WHI 8      /* 2700 Hz */
#define KMIX_STEP 6711 /* the mixer's step for FT8_STEP Hz: 150 x 2^32 / 96 MHz */
#define FL_ROWS 50     /* rows (40 ms) between flushes: 2 s, each a 20-40 ms gap in the IF */

#define RSZ 512u
#define GP0RX_HZ(api, r) ((r)->hz)
#include "gp0rx.h"
#define UI_UDIV fk_udiv
#define UI_LOG_N 2
#include "k6ui.h"

/* the FT8 dial frequencies the K6 hears (and HERE: the pager's) */
static const uint32_t BAND_HZ[3] = {50313000u, 144174000u, 432174000u};
static const char BAND_NAME[4][5] = {"6M", "2M", "70CM", "HERE"};

/* the screen's state, in the scratch (the stack has the waterfall's 2 KB already) */
typedef struct {
  ui_t u;
  uint8_t acc[FT8_NB];          /* each bin's highest value since the last flush */
  uint8_t band, scan;
  int8_t w;                     /* the window: steps of FT8_STEP from FT8_AUDIO */
} fu_t;

static uint32_t band_hz(const k6_api_t *api, int b) { return b < 3 ? BAND_HZ[b] : api->rx_hz(); }

/* the title: the band and its dial */
__attribute__((noinline)) static void fu_title(fu_t *f, uint32_t hz) {
  char l[24], *p = ui_put(l, "FT8 ");
  p = ui_put(p, BAND_NAME[f->band]);
  *p++ = ' ';
  ui_mhz(p, hz);
  ui_text(&f->u, 0, l);
}

/* another band (keys 1-4): there, the calibration for it, the window kept */
__attribute__((noinline)) static void fu_band(fu_t *f, const k6_api_t *api, rx_t *r, int b) {
  f->band = (uint8_t)b;
  r->hz = band_hz(api, b);
  api->tune(r->hz, api->fd_read(0x43) == 0x3028);
  rx_cal(api, r);
  r->kmix += (uint32_t)(f->w * KMIX_STEP);
  fu_title(f, r->hz);
}

/* the screen's line 3 (the window's bins 1-21, their highest since the last flush, in ft8k's
   steps: the floor at 4, 1.5 dB a step; '.' 3 dB over it, ':' 6, '|' 9) and line 5 (seconds,
   the window's centre, decodes, SCAN) */
__attribute__((noinline)) static void fu_show(fu_t *f, uint32_t rows, uint32_t ndec) {
  char a[24], *p;
  int j;
  for (j = 0; j < 21; j++) {
    int v = f->acc[j + 1];
    a[j] = v >= 10 ? '|' : v >= 8 ? ':' : v >= 6 ? '.' : ' ';
    f->acc[j + 1] = 0;
  }
  a[21] = 0;
  ui_text(&f->u, 3, a);
  p = ui_dec(a, fk_udiv(rows, 25), 1);
  p = ui_put(p, "S ");
  p = ui_dec(p, (uint32_t)(FT8_AUDIO + f->w * FT8_STEP), 1);
  p = ui_put(p, "HZ D");
  p = ui_dec(p, ndec, 1);
  if (f->scan) ui_put(p, " SCAN");
  ui_text(&f->u, 5, a);
  ui_now(&f->u);
}

/* a decode into the screen's log: its text (m up to e) and its SNR (its own function: the
   buffer isn't in k6_main's frame, under the LDPC's) */
__attribute__((noinline)) static void fu_dec(fu_t *f, const char *m, const char *e, int snr) {
  char t[24], *q = t;
  while (m < e && q < t + 17) *q++ = *m++;
  *q++ = ' ';
  ui_sdec(q, snr);
  ui_log(&f->u, t);
}

/* the newest waterfall row (ft8k's, 4 bits a bin) into the bins' highest */
__attribute__((noinline)) static void fu_row(fu_t *f, const fk_t *k) {
  const uint8_t *row = k->wf + (k->slot ? k->slot - 1 : FK_ROWS - 1) * (FT8_NB >> 1);
  int j;
  for (j = 0; j < FT8_NB; j++) {
    uint8_t v = (uint8_t)((row[j >> 1] >> ((j & 1) * 4)) & 15);
    if (v > f->acc[j]) f->acc[j] = v;
  }
}

int k6_main(const k6_api_t *api) {
  fk_t *k;
  int16_t *sc;
  uint8_t *work, *ring, wf[FK_WF_BYTES(FT8_NB)];
  saved_t sv;
  rx_t r;
  uint32_t room, r2, next, stat = 0, ndec = 0;
  uint8_t *s;
  char line[64], *p;
  int ret;
  fu_t *f;
  uint32_t nrow, fl = 0, hop = 0, limit = (uint32_t)FT8_SECONDS * 25u;
  /* the scratch: the DMA ring, the receiver, its scores, the LDPC's work and the screen */
  s = (uint8_t *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  if (room < RSZ + sizeof(fk_t) + 2 * FK_SC_WORDS(FT8_NB) + FK_WORK + sizeof(fu_t) + 12) return -8;
  ring = s;
  k = (fk_t *)(s + RSZ);
  sc = (int16_t *)((uint8_t *)k + ((sizeof(fk_t) + 3) & ~3u));
  work = (uint8_t *)sc + ((2 * FK_SC_WORDS(FT8_NB) + 3) & ~3u);
  f = (fu_t *)(((uint32_t)work + FK_WORK + 3) & ~3u);
  ui_begin(&f->u, api, "FT8 RX", "TUNING");
  f->w = 0, f->band = 3, f->scan = 0;
  for (ret = 0; ret < FT8_NB; ret++) f->acc[ret] = 0;
  r.hz = api->rx_hz();
  if (f->u.on) {
    /* the screen: straight to 2 m, listening until EXIT, the window scanning */
    f->band = 1, f->scan = 1;
    r.hz = BAND_HZ[1];
    limit = FT8_UI_ROWS;
    f->u.lt = 1, f->u.lb = 2; /* the decodes' log: lines 1-2 */
    fu_title(f, r.hz);
    ui_now(&f->u);
  }
  ret = rx_setup(api, &sv, &r, ring);
  if (ret) return ret;
  p = rx_gain(api, &sv, &r, &r2, sdec(put(line, "ft8 start cal="), r.cal));
  say0(api, line, p);
  if (f->u.on) {
    ui_text(&f->u, 1, "LISTENING");
    fu_show(f, 0, 0);
  }
  fk_init(k, FT8_NB, (int)r2, wf, sc, work);
  RX_FK(k);
  resync(&r);
  next = r.t + ROW_CYC;
  while (r.rows < limit) {
    drain(&r, k, 0);
    if ((int32_t)(r.t - next) < 0) continue;
    rx_rate(&r);
    next += ROW_CYC;
    r.rows++;
    nrow = k->nrow;
    if (fk_row(k)) {
      int j;
      for (j = 0; j < k->ndec; j++) {
        const fk_dec_t *d = &k->dec[j];
        /* tone 0: the window's centre + (bin - nb/2) bins of about 6.25 Hz */
        p = fk_text(put(line, "ft8 "), d->msg);
        if (f->u.on) {
          fu_dec(f, line + 4, p, d->snr);
          fl = FL_ROWS; /* on the screen now */
        }
        p = put(p, " f=");
        p = sdec(p, FT8_AUDIO + f->w * FT8_STEP + ((d->bin - FT8_NB / 2) * 25) / 4);
        p = put(p, " s=");
        p = sdec(p, d->snr);
        p = put(p, " t=");
        p = tenths(p, fk_udiv(d->row * 8, 10)); /* rows of 80 ms since the start */
        say0(api, line, p);
        ndec++;
      }
    }
    if (f->u.on) {
      int key, mv = 0;
      if (k->nrow != nrow) fu_row(f, k);
      /* SCAN: the next window every FT8_HOP_S seconds */
      if (f->scan && ++hop >= (uint32_t)FT8_HOP_S * 25u) mv = 1;
      /* the keys once a row, never in the drain loop (as the stop key below) */
      key = ui_key(&f->u);
      if (key == 'X') break;
      if (key == 'U') mv = 1, f->scan = 0;
      if (key == 'D') mv = -1, f->scan = 0;
      if (key == '5') f->scan ^= 1, hop = 0, fl = FL_ROWS;
      if (key >= '1' && key <= '4') fu_band(f, api, &r, key - '1'), mv = 2;
      if (mv) {
        /* another window (or band): the old rows mean nothing there */
        if (mv != 2) {
          int w = f->w + mv;
          if (w > FT8_WHI) w = FT8_WLO;
          if (w < FT8_WLO) w = FT8_WHI;
          r.kmix += (uint32_t)((w - f->w) * KMIX_STEP);
          f->w = (int8_t)w;
        }
        hop = 0, fl = FL_ROWS;
        fk_init(k, FT8_NB, (int)r2, wf, sc, work);
        RX_FK(k);
      }
      if (++fl >= FL_ROWS) {
        fl = 0;
        fu_show(f, r.rows, ndec);
        resync(&r);
      }
    }
    if (++stat == FT8_STAT) {
      stat = 0;
      p = kv(line, "ft8 st t=", fk_udiv(r.rows, 25));
      p = kv(p, " lost=", r.lost);
      p = kv(p, " ks=", fk_udiv(96000u * 256u, r.cps8));
      p = kv(p, " dec=", ndec);
      p = kv(p, " flo=", (uint32_t)(k->flo >> 4));
      p = kv(p, " w=", (uint32_t)(FT8_AUDIO + f->w * FT8_STEP));
      say0(api, line, p);
    }
    /* the stop key once a row, never in the drain loop: Aura scans the keypad with six
       5 us waits, about 60 us on the K6, and that each pass overran the ring (run 1) */
    if (!f->u.on && api->key_down()) break;
    resync(&r);
  }
  teardown(api, &sv);
  p = kv(line, "ft8 done rows=", r.rows);
  p = kv(p, " dec=", ndec);
  p = kv(p, " lost=", r.lost);
  say0(api, line, p);
  return (int)ndec;
}
