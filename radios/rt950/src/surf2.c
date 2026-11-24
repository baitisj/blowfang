/* surf2: band surfing with two receivers, on the RT-950 Pro. Receive only.

   SURF on the K6 (c/k6/mod/surf.c, Jeff's idea of 2026-11-08) has one receiver for both jobs,
   finding signals and listening to one, and most of its compromises come from that: it sweeps
   only while the listened channel is quiet, mutes the speaker on every retune, stops the carets
   while it listens, and misses short bursts when its confirm read lands in a gap. The RT-950 Pro
   has two BK4829s, so the jobs split (Jeff, 2026-11-16; docs/rt950-plan.md, "Dual-chip SURF"):

   - Chip 1 (PE15) sweeps, all the time, and never makes audio. It reads every bin of the lens
     scale with the same quick and confirm reads as the K6, and feeds the bars and the carets.
     It stays in FM at the normal IF whatever the listener does, so the bars keep one scale
     when key 4 puts the listener in DSB or AM.
   - Chip 0 (PE8) listens: it sits on the middle frequency with the audio path and the squelch,
     polled between sweep reads. The side keys, UP / DOWN, 5 and BAND retune only chip 0.
   - The sweep skips a guard band round chip 0 (GUARD_HZ, +-25 kHz to start, set by measuring
     how a sweeping chip desenses the listener on Andrew's radio). Bins inside the listener's
     own filter (+-LHALF_HZ) take chip 0's level, put on chip 1's quick-read scale; the rest of
     the guard is held (flag SURF2_HELD) and falls slowly, with no carets from it.
   - A side-key jump to a caret that only a coarse outer bin heard needs the lens read there
     before the listener goes (the K6's refine). Chip 1 reads it while chip 0 is still on the
     old frequency, so the guard doesn't get in the way, and chip 0 then goes straight to the
     station.
   - The squelch is chip 0's settled level SQ_DB over its own floor: chip 1's settled floor plus
     a per-chip offset, measured at the start, at BAND and after a gain or mode change by
     reading the same 8 bins on both chips (chip 1 off while chip 0 reads, so its LO isn't in
     the way), and lowered by any quieter read. That is the plan's "free per-chip RSSI check".
   - PTT (keys 'P' and 'p' from the glue): the sweep stops and chip 1's front end goes off
     until release, the rule for any two-chip app around a transmission. SURF itself never
     transmits.
   - Hold (0) stops the sweep (the carets stop falling with it); monitor (*) opens chip 0's
     squelch all the way and keeps the audio on, and the sweep goes on. Fixed gain (7 / 8 / 9)
     applies to both chips, so the offset between them stays measured.

   Everything else (the scale, the bars, the caret rules Jeff signed off on 2026-11-12, the jumps)
   is the K6's own code, shared through c/common/surf_core.inc (MIT, included here).

   Not known until the radio is on the bench (each is a constant here, see radios/rt950/README.md):
   how long a BK4829 RSSI read takes to settle after a tune (SETTLE_US, CHECK_US, QUICK_PCT),
   REG_67's dBm offset (the glue's, RT_RSSI_OFS), the guard (GUARD_HZ), and whether both chips can
   hear at once through the shared front end (the band relay and LNA follow chip 0, so chip 1
   only sweeps chip 0's side of 300 MHz; SURF's bands are each on one side).

   Lines (api->say): "surf2 70cm 440.850 25k", "sel 441.2000 -71 -127" (MENU: the middle, chip 0's
   dBm, its floor), "loff 3" (the chips' measured offset, dB), "end sw=N".
   SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdint.h>
#include "rt_api.h"
#include "surf2.h"

#ifndef GUARD_HZ
#define GUARD_HZ 25000u         /* chip 1 never tunes closer than this to chip 0 */
#endif
#ifndef LHALF_HZ
#define LHALF_HZ 12500u         /* chip 0's wide filter: bins this close take its level */
#endif
#ifndef QUICK_PCT
#define QUICK_PCT 59            /* a quick read gets this % of the way (in dB) from -173 dBm to the
                                   settled level (the K6's FD6818, Phase 0); the BK4829's: to measure */
#endif
#define FLUSH_MS 200            /* a frame this often */
#define IDLE_US 2000            /* a loop with no sweep (hold, PTT) waits this long */
#define ROWS 1                  /* (sf_t's K6 bitmap: not used here) */
#define NK 1                    /* (sf_t's repeater table: not used here yet) */
#define SF_API rt_api_t
/* a bin chip 1 must not read: inside the guard round where chip 0 is tuned */
#define SF_SKIP(s, i) ((s)->lhz && (bin_hz(s, i) > (s)->lhz ? bin_hz(s, i) - (s)->lhz : (s)->lhz - bin_hz(s, i)) < GUARD_HZ)
#define SF_EXTRA                                                                          \
  uint32_t lhz;                 /* where chip 0 is tuned (0: not yet) */                  \
  uint32_t t_ltune;             /* when, ms */                                            \
  uint32_t swhz;                /* where chip 1 is tuned */                               \
  int loff;                     /* chip 0's settled floor minus chip 1's, dB */           \
  int ldbm;                     /* chip 0's last read */                                  \
  uint32_t r13b, r7eb;          /* chip 1's gain registers as the firmware had them */    \
  uint8_t lok;                  /* chip 0 has settled since its tune */                   \
  uint8_t gq;                   /* the last sweep read was inside the guard: not a read */ \
  uint8_t ptt;
#include "../../../c/common/surf_core.inc"

/* the bands: the K6's, which each sit on one side of the front end's 300 MHz split */
static const band_t BANDS[] = {
    {144000000u, 148000000u, 146000000u, ST_10K, 2, 0, "2m"},
    {222000000u, 225000000u, 223500000u, ST_10K, 2, 0, "1.25m"},
    {430000000u, 450000000u, 440000000u, ST_25K, 3, 0, "70cm"},
    {462550000u, 467725000u, 465137500u, ST_12K5, 4, 0, "GMRS"},
    {156000000u, 162550000u, 162475000u, ST_25K, 3, 0, "MAR/WX"},
    {118000000u, 137000000u, 127500000u, ST_25K, 3, 1, "AIR"}};
#define NBANDS 6

/* ---- chip 1: the sweep ---- */

/* chip 1's level at hz, us after its tune; a key pressed meanwhile is kept (s->qk), so a long
   sweep doesn't lose it. Inside the guard it doesn't tune at all: the bins there are chip 0's */
static int read_hz(sf_t *s, uint32_t hz, uint32_t us) {
  const rt_api_t *api = s->api;
  int k;
  if (s->lhz && (hz > s->lhz ? hz - s->lhz : s->lhz - hz) < GUARD_HZ) {
    s->gq = 1;
    return s->qfl ? s->qfl - 174 : s->sfl;
  }
  s->gq = 0;
  api->rf_tune(RT_CHIP_SWEEP, hz);
  s->swhz = hz;
  api->delay_us(us);
  if (!s->qk && (k = api->key()) != 0) s->qk = (uint8_t)k;
  return api->rf_dbm(RT_CHIP_SWEEP);
}

static int dbm(sf_t *s) { return s->gq ? (s->qfl ? s->qfl - 174 : s->sfl) : s->api->rf_dbm(RT_CHIP_SWEEP); }

/* ---- chip 0: the listener ---- */

static void lis_audio(sf_t *s, int on) {
  if (on == s->audio) return;
  s->audio = (uint8_t)on;
  s->api->rf_write(RT_CHIP_LISTEN, 0x47, (uint16_t)(on ? s->af : AF_MUTE));
  s->api->speaker(on);
}

/* chip 0 onto the middle; the audio shuts while it retunes and its level settles */
static void lis_tune(sf_t *s) {
  lis_audio(s, 0);
  s->api->rf_tune(RT_CHIP_LISTEN, s->cen);
  s->lhz = s->cen;
  s->t_ltune = s->api->ms();
  s->lok = 0;
}

/* the middle moved (a key, a jump, a band). A jump to a caret only a coarse bin heard: chip 1
   reads the lens there first, while chip 0 is still away, and the refine may move the middle
   once more (refine_lens calls this again, with refine cleared) */
static void moved(sf_t *s) {
  int i;
  if (s->refine && !s->ptt) {
    for (i = C - KI; i <= C + KI && !s->qk; i++)
      if (bin_hz(s, i) && !SF_SKIP(s, i)) sweep_bin(s, i);
    if (!s->qk && refine_lens(s)) return;
  }
  s->refine = 0;
  lis_tune(s);
}

/* the demodulator, on chip 0 only (AM: AF 7 and the AFC off; DSB: AF 5 at the zero IF, REG_3D 0;
   FM: REG_73 as the firmware had it), as the K6's demod */
static void demod(sf_t *s) {
  const rt_api_t *api = s->api;
  int md = s->md;
  s->af = (uint16_t)(md == MD_AM ? AF_AM : md == MD_DSB ? AF_DSB : AF_FM);
  if (md == MD_DSB || s->zif) {
    s->zif = 1;
    api->rf_write(RT_CHIP_LISTEN, 0x3D, (uint16_t)(md == MD_DSB ? 0 : s->r3d));
  }
  api->rf_write(RT_CHIP_LISTEN, 0x73, (uint16_t)((s->r73 & ~0x10u) | (md != MD_FM ? 0x10u : 0)));
  if (s->audio) api->rf_write(RT_CHIP_LISTEN, 0x47, s->af);
}

/* monitor: chip 0's own squelch open all the way, or put back */
static void squelch(sf_t *s, int on) {
  const rt_api_t *api = s->api;
  int k;
  if (on)
    for (k = 0; k < 4; k++) s->sq[k] = api->rf_read(RT_CHIP_LISTEN, SQ_REGS[k]);
  api->rf_write(RT_CHIP_LISTEN, 0x4D, on ? (uint16_t)((s->sq[0] & 0xFF00u) | 0xFFu) : s->sq[0]);
  api->rf_write(RT_CHIP_LISTEN, 0x4E, on ? (uint16_t)((s->sq[1] & 0xFF00u) | 0xFFu) : s->sq[1]);
  api->rf_write(RT_CHIP_LISTEN, 0x4F, on ? 0x7F7Fu : s->sq[2]);
  api->rf_write(RT_CHIP_LISTEN, 0x78, on ? 0x0000u : s->sq[3]);
}

/* the gain on both chips: the AGC as the firmware had it (gx 0), or fixed at GAINS[gx - 1] */
static void gain(sf_t *s) {
  const rt_api_t *api = s->api;
  char *p = put(s->msg, "gain ");
  int c;
  for (c = 0; c < 2; c++) {
    uint32_t r13 = c ? s->r13b : s->r13, r7e = c ? s->r7eb : s->r7e;
    api->rf_write(c, 0x13, (uint16_t)(s->gx ? GAINS[s->gx - 1] : r13));
    api->rf_write(c, 0x7E, (uint16_t)(s->gx ? (r7e & 0x0FFFu) | 0xB000u : r7e));
  }
  if (s->gx) p = put(sint(p, GAIN_DB[s->gx - 1]), " dB");
  else p = put(p, "auto");
  *p = 0;
  s->t_msg = s->now + MSG_MS;
}

/* chip 0's floor against chip 1's: the second lowest of the same 8 bins' settled reads that
   measure_sfl takes on chip 1, with chip 1 off meanwhile; then chip 0 back on the middle */
static void measure_loff(sf_t *s) {
  const rt_api_t *api = s->api;
  int j, d, a = 999, b = 999;
  char *p;
  lis_audio(s, 0);
  api->rf_off(RT_CHIP_SWEEP);
  for (j = 3; j < NV; j += 8) {
    if (!bin_hz(s, j)) continue;
    api->rf_tune(RT_CHIP_LISTEN, bin_hz(s, j));
    s->lhz = bin_hz(s, j);
    api->delay_us(CHECK_US);
    d = api->rf_dbm(RT_CHIP_LISTEN);
    if (d < a) b = a, a = d;
    else if (d < b) b = d;
  }
  if (a < 999) s->loff = (b < 999 ? b : a) - s->sfl;
  p = put(s->ln, "loff ");
  say_end(s, sint(p, s->loff));
  lis_tune(s);
}

/* chip 0's level, between sweep reads: the squelch (SQ_DB over its floor opens, HANG_MS after
   it drops below the hysteresis closes) and a caret for what plays */
static void lis_poll(sf_t *s) {
  int d, fl;
  if (!s->lhz || s->ptt) return;
  if (!s->lok) {
    if (s->now - s->t_ltune < CHECK_US / 1000u) return;
    s->lok = 1;
  }
  d = s->ldbm = s->sel_dbm = s->api->rf_dbm(RT_CHIP_LISTEN);
  fl = s->sfl + s->loff;
  if (d < fl) s->loff = d - s->sfl, fl = d;   /* a quieter read lowers the floor */
  if (d >= fl + SQ_DB) {
    pm_heard(s);
    s->t_open = s->now;
    lis_audio(s, 1);
  } else if (d >= fl + SQ_DB - HYST_DB) {
    s->t_open = s->now;
  }
  if (s->mon) lis_audio(s, 1);
  else if (s->audio && s->now - s->t_open > HANG_MS) lis_audio(s, 0);
}

/* after a whole sweep: the guard's bins. Inside chip 0's filter they take its level, put on chip
   1's quick scale (and the per-chip offset taken off); the rest fall DECAY_DB a sweep towards
   the floor. None of them counts as heard, so no caret comes from them (chip 0's own come from
   pm_heard) */
static void guard_fill(sf_t *s) {
  int i, q = 0;
  if (s->lok) {
    q = s->ldbm - s->loff + 173;
    q = q > 0 ? q * QUICK_PCT / 100 + 1 : 1;   /* (dBm + 174) */
    if (q > 255) q = 255;
  }
  for (i = 0; i < NV; i++) {
    uint32_t d;
    if (!bin_hz(s, i) || !SF_SKIP(s, i)) continue;
    s->heard[i >> 5] &= ~(1u << (i & 31));
    s->cf[i] = 0;
    d = bin_hz(s, i) > s->lhz ? bin_hz(s, i) - s->lhz : s->lhz - bin_hz(s, i);
    if (d <= LHALF_HZ && q) {
      s->lvl[i] = (uint8_t)q;
      s->pk[i] = bin_hz(s, i);
    } else if (!s->lvl[i]) {
      s->lvl[i] = s->qfl;               /* (never read: at the floor, not blank) */
    } else if (s->lvl[i] > s->qfl) {
      s->lvl[i] = (uint8_t)(s->lvl[i] - s->qfl > DECAY_DB ? s->lvl[i] - DECAY_DB : s->qfl);
    }
  }
}

static void sweep_step(sf_t *s) {
  int i = s->next;
  if (bin_hz(s, i) && !SF_SKIP(s, i)) sweep_bin(s, i);
  if (++s->next >= NV) {
    s->next = 0;
    guard_fill(s);
    sweep_done(s);
    if (s->now - s->t_sfl >= SFL_MS) measure_sfl(s);
  }
}

static void ptt(sf_t *s, int on) {
  if (on == s->ptt) return;
  s->ptt = (uint8_t)on;
  if (on) {
    lis_audio(s, 0);
    s->api->rf_off(RT_CHIP_SWEEP);
  } else {
    lis_tune(s);                        /* (chip 1 comes back on at its next tune) */
  }
}

static void on_key(sf_t *s, int k) {
  switch (k) {
  case 'U': go_to(s, s->cen + s->ch); break;
  case 'D': go_to(s, s->cen - s->ch); break;
  case '5': go_to(s, s->bd->home); break;
  case 'A': jump(s, 1); break;
  case 'B': jump(s, -1); break;
  case 'W':
    s->fall = (uint8_t)(s->fall + 1 == NFALL ? 0 : s->fall + 1);
    put(put(s->msg, "fall "), FALL[s->fall])[0] = 0;
    s->t_msg = s->now + MSG_MS;
    break;
  case 'N':
    band(s, s->bd + 1 == BANDS + NBANDS ? BANDS : s->bd + 1);
    measure_loff(s);
    break;
  case '#':
    s->st = (uint8_t)(s->st + 1 == NSTEPS ? 0 : s->st + 1);
    s->ch = STEPS[s->st];
    note(s, "step");
    stepn(s->msg + 5, s->ch)[0] = 0;
    s->msg[4] = ' ';
    break;
  case '7':
  case '9':
    if (!s->gx) s->gx = k == '7' ? 2 : 1;
    else if (k == '7' ? s->gx < NGAINS : s->gx > 1) s->gx = (uint8_t)(s->gx + (k == '7' ? 1 : -1));
    /* fall through */
  case '8':
    if (k == '8') s->gx = 0;
    gain(s);
    measure_sfl(s);
    measure_loff(s);
    s->stale = 1;
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
    break;
  case '0':
    s->hold ^= 1;
    s->parked = s->hold;                /* (pm_fall: the carets stop while the sweep does) */
    break;
  case '4':
    s->md = (uint8_t)(s->md + 1 == NMD ? 0 : s->md + 1);
    demod(s);
    note(s, MD_NAME[s->md]);
    measure_loff(s);                    /* (chip 0's floor at the zero IF is another) */
    break;
  case 'M': say_sel(s); break;
  case 'P': ptt(s, 1); break;
  case 'p': ptt(s, 0); break;
  }
}

static void show(sf_t *s, rt_view_t *v) {
  int i, k;
  s->t_flush = s->now;
  v->band = s->bd->name;
  v->mode = MD_NAME[s->md];
  v->msg = (int32_t)(s->t_msg - s->now) > 0 ? s->msg : 0;
  v->step = s->ch;
  v->cen = s->cen;
  v->span = U_HZ * SPANS[s->si];
  spanx(v->span_txt, v->span)[0] = 0;
  v->sweep_hz = s->swhz;
  v->sweeps = (uint32_t)s->sweeps;
  v->dbm = s->sel_dbm;
  v->floor_dbm = s->sfl + s->loff;
  v->gain_db = s->gx ? GAIN_DB[s->gx - 1] : 1;
  v->audio = s->audio;
  v->mon = s->mon;
  v->hold = s->hold;
  v->ptt = s->ptt;
  v->floor_q = s->qfl;
  for (i = 0; i < NV; i++) {
    uint32_t d = bin_hz(s, i) > s->lhz ? bin_hz(s, i) - s->lhz : s->lhz - bin_hz(s, i);
    v->hz[i] = bin_hz(s, i);
    v->lvl[i] = s->lvl[i];
    v->caret[i] = 0;
    v->flags[i] = (uint8_t)(!bin_hz(s, i) || !SF_SKIP(s, i) ? 0 : d <= LHALF_HZ ? SURF2_LISTEN : SURF2_HELD);
  }
  for (k = 0; k < s->npm; k++) {
    pm_t *e = &s->pm[k];
    if (!e->h || e->bad || (i = bin_of(s, e->f)) < 0) continue;
    if (e->h > v->caret[i]) v->caret[i] = e->h;
  }
  s->api->show(v);
}

uint32_t surf2_ram(void) { return sizeof(sf_t) + sizeof(rt_view_t) + 8u * 16u; }

int surf2_run(const rt_api_t *api, void *ram, uint32_t room, uint32_t hz) {
  sf_t *s = (sf_t *)ram;
  rt_view_t *v;
  uint8_t *z = (uint8_t *)ram;
  uint32_t i;
  int k;
  char *p;
  if (room < surf2_ram()) return -4;
  for (i = 0; i < sizeof(sf_t) + sizeof(rt_view_t); i++) z[i] = 0;
  v = (rt_view_t *)(z + sizeof(sf_t));
  room -= (uint32_t)(sizeof(sf_t) + sizeof(rt_view_t));
  s->api = api;
  s->bmp = s->bm;
  s->fall = 1;
  s->pm = (pm_t *)(z + sizeof(sf_t) + sizeof(rt_view_t));
  s->npm = room >> 3 < NPM ? (int)(room >> 3) : NPM;
  for (k = 0; k < s->npm; k++) s->pm[k].h = 0;
  s->r47 = api->rf_read(RT_CHIP_LISTEN, 0x47);
  s->r13 = api->rf_read(RT_CHIP_LISTEN, 0x13);
  s->r73 = api->rf_read(RT_CHIP_LISTEN, 0x73);
  s->r7e = api->rf_read(RT_CHIP_LISTEN, 0x7E);
  s->r3d = api->rf_read(RT_CHIP_LISTEN, 0x3D);
  s->r13b = api->rf_read(RT_CHIP_SWEEP, 0x13);
  s->r7eb = api->rf_read(RT_CHIP_SWEEP, 0x7E);
  s->af = AF_FM;
  s->audio = 1;
  lis_audio(s, 0);
  s->bd = &BANDS[2];
  for (i = 0; i < NBANDS; i++)
    if (hz >= BANDS[i].lo && hz <= BANDS[i].hi) s->bd = &BANDS[i];
  s->st = s->bd->st;
  s->si = s->bd->si;
  s->md = s->bd->am ? MD_AM : MD_FM;
  s->now = api->ms();
  s->cen = hz;
  lis_tune(s);                          /* (chip 0 first: the guard holds from the first read) */
  demod(s);
  window(s, hz);
  measure_loff(s);
  p = put(s->ln, "surf2 ");
  p = put(p, s->bd->name);
  *p++ = ' ';
  p = mhz(p, hz, 3);
  *p++ = ' ';
  say_end(s, stepn(p, s->ch));
  s->t_flush = api->ms() - FLUSH_MS;
  for (;;) {
    s->now = api->ms();
    pm_fall(s);
    if (s->qk) k = s->qk, s->qk = 0;
    else k = api->key();
    if (k == 'X') break;
    if (k) {
      on_key(s, k);
      s->now = api->ms();
      show(s, v);
    }
    if (s->hold || s->ptt) api->delay_us(IDLE_US);
    else sweep_step(s);
    s->now = api->ms();
    lis_poll(s);
    if (s->now - s->t_flush >= FLUSH_MS) show(s, v);
  }
  /* put back both chips and the audio; chip 0 back on where it started, chip 1 off */
  api->rf_write(RT_CHIP_LISTEN, 0x47, (uint16_t)s->r47);
  api->rf_write(RT_CHIP_LISTEN, 0x13, (uint16_t)s->r13);
  api->rf_write(RT_CHIP_LISTEN, 0x73, (uint16_t)s->r73);
  api->rf_write(RT_CHIP_LISTEN, 0x7E, (uint16_t)s->r7e);
  if (s->zif) api->rf_write(RT_CHIP_LISTEN, 0x3D, (uint16_t)s->r3d);
  if (s->mon) squelch(s, 0);
  api->rf_write(RT_CHIP_SWEEP, 0x13, (uint16_t)s->r13b);
  api->rf_write(RT_CHIP_SWEEP, 0x7E, (uint16_t)s->r7eb);
  api->rf_off(RT_CHIP_SWEEP);
  api->speaker(0);
  api->rf_tune(RT_CHIP_LISTEN, hz);
  p = put(s->ln, "end sw=");
  say_end(s, fix(p, (uint32_t)s->sweeps, 0));
  return s->sweeps;
}
