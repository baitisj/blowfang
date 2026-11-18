/* refcal: the K6's crystal error against carriers that are always on the air, with the MCU's
   temperature (Jeff, 2026-11-17; the plan is the Claude Doc "K6 frequency drift calibration",
   docs/k6-drift-cal.md). Receive only.

   The K6 has a plain 26 MHz crystal, so its error is the same in ppm on every band and moves
   with temperature (on 2026-11-16 the K6 stopped decoding 440.850 uploads as the garage cooled).
   ifcal measures it against a bench carrier on 440.850; refcal measures it off the air, where the
   best references are UHF TV pilots (an ATSC pilot is a bare carrier 309.440559 kHz over its 6 MHz
   channel's lower edge, on all the time; the Pluto found four at hamlet that agree within 0.1 ppm,
   2026-11-17 00:50Z) and then NOAA / Canadian weather radio (FM voice: averaged longer).

   Steps:
     1  the MCU's temperature sensor and VREFINT (ADC channels 16 and 17, 16 reads each; CCR
        TSEN and VREFEN set for the reads and put back). There is no factory calibration, so the
        two raw sums are a relative index for a drift table; "t=0" if Aura left the ADC off.
     2  the scan: every reference (23 ATSC pilots on RF 14-36, the 7 weather channels, or
        REFCAL_HZ alone), a settled RSSI on it and on the quieter of +-400 kHz, narrow filter:
        "refcal s 470309.44 -71 -104" (kHz, dBm on it, dBm beside it). REFCAL_SCAN_ONLY ends here.
     3  the AFC off (REG_73 bit 4), the wide filter (REFCAL_NARROW keeps the narrow one), and the
        strongest reference REFCAL_SNR dB over its side: the mean IF over REFCAL_BLOCKS of
        gp0dma's blocks (52 ms each). Then REG_3B + REFCAL_PROBE and again: the IF's move tells which way a step turns it (the chip mixes high or low),
        and its size must be within 40-160 % of what one step means at that frequency (5 Hz of
        the crystal's 26 MHz: 0.192 ppm, 85 Hz at 440.850, 90 Hz at 470.309). If it isn't (on
        2026-11-17 RF 14's pilot at -105 dBm on the K6's stub moved 27 %: its own 8VSB data
        filled the IF filter), the next strongest reference is probed, up to REFCAL_PROBES.
     4  null-seeking: REG_3B at the step that should put the carrier on the nominal IF
        (26 MHz / 1536), and the IF again, up to 3 times. Near the null the read is unbiased: at
        low SNR the noise in the IF filter pulls a plain mean towards the filter's middle, which
        is the nominal IF. The error at the old REG_3B is then the residual less the steps' move.
     5  up to REFCAL_NREF more references (the next strongest), each read once at the null; the
        answer is their median (the Pluto saw single pilots off by up to 6 ppm).
   A reference's own known offset (REFCAL_KNOWN, Hz from the Pluto: {kHz x 100, 0.1 Hz}) is
   taken off; the 10 Hz tuning raster's 0.56 Hz short of an ATSC pilot is taken off too.

   Lines (K6 MOD say ...):
     refcal t=27731 v=24640 3b=5880 3c=4f88                  (temperature and VREFINT sums)
     refcal s 470309.44 -71 -104                              (each reference scanned)
     refcal m 470309.44 if=16926.3 n=32 sp=4.1 k=0            (a mean IF: 0.1 Hz, blocks, spread)
     refcal step 470309.44 d=+539.8 g=90.4                    (the probe: the move, one step's)
     refcal r 470309.44 ppb=-335 res=+1.2 k=-2                (one reference's error at the null)
     refcal ppb=-335 n=3 k=-2 t=27731 dry                     (the median, the trim, "dry"/"set")
   ppb is the crystal's own error, + fast (every LO, receive and transmit, that much high), which
   does not depend on whether the chip mixes high or low; k is the REG_3B trim that cancels it
   (a step is 192 ppb, larger REG_3B = LO down). ifcal's offset is the IF's, so its sign is the
   probe's d times this one's.
   REFCAL_APPLY leaves REG_3B at the null (k steps from where it was): Aura never writes REG_3B
   (it corrects only through the frequency word), so the trim holds for everything the radio
   does, receive and transmit, until power-off. Without it (the default: a dry run) REG_3B goes
   back. Nothing is written to flash yet (the K6CL record's IF offset is the bench pair's; a
   drift table in its sector comes after the bench runs). Writes REG_73, REG_3B and what gp0dma
   writes (REG_33, REG_34, TIM17, DMA channel 1, their clocks), the ADC's CCR, SMPR and CHSELR,
   all put back. Returns the median ppb, or gp0dma's errors (-2 key, -3 image below level 4,
   -4 PB1, -5 REG_33, -7 TIM17 / DMA busy, -8 scratch), -6 no reference REFCAL_SNR dB up, -12
   no reference's probe moving one step's worth (up to REFCAL_PROBES tried; nothing kept). SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 4
#define K6_MODULE_OPT z /* the DMA ring needs 516 B of the store past the code */
#define GP0_SWEEP 1
#if defined(REFCAL_HZ) && !defined(REFCAL_WAIT)
/* the Pluto relay: the carrier to measure follows the upload in the same key-up, so no waiting
   for the upload's carrier to go (bench 2026-11-17 05:06-05:14Z: that wait outlasted the tail) */
#define GP0_NO_WAIT 1
#endif
/* REFCAL_WAIT (with REFCAL_HZ): the carrier comes in a key-up of its own after the upload (the
   bench runner's steps, 2026-11-18): wait for the upload's carrier to go, then up to 90 s for
   the next one (gp0dma's carrier(): 15 dB over the floor between them). REFCAL_HZ also reads
   the one reference REFCAL_NREF times at the null (a median of the reads, not of references) */
#define GP0_NO_MAIN 1
#define TAG "refcal"
#include "gp0dma.c"
#include "k6_cal.h"

#ifndef REFCAL_BLOCKS
#define REFCAL_BLOCKS 32 /* a mean IF: about 1.7 s */
#endif
#ifndef REFCAL_SNR
#define REFCAL_SNR 10    /* dB over the quieter side */
#endif
#ifndef REFCAL_PROBES
#define REFCAL_PROBES 3 /* references tried for the probe */
#endif
#ifndef REFCAL_PROBE
#define REFCAL_PROBE 6   /* REG_3B steps for the probe */
#endif
#ifndef REFCAL_SETTLE_MS
#define REFCAL_SETTLE_MS 500  /* REFCAL_WAIT: after the steady carrier, before the scan */
#endif
#ifndef REFCAL_HOLD_MS
#define REFCAL_HOLD_MS 2000   /* REFCAL_WAIT: a carrier on this long without a break */
#endif
#ifndef REFCAL_NREF
#define REFCAL_NREF 4    /* references read at the null, the first included */
#endif
#define KMAX 100         /* the trim: +-100 steps, +-19 ppm */
#define NOM_DHZ 169271   /* 26 MHz / 1536 in 0.1 Hz */
#define NREFS 30
#define SIDE_HZ 400000u
#define ADC 0x40012400u
#define ADC_ISR (ADC + 0x00)
#define ADC_CR (ADC + 0x08)
#define ADC_SMPR (ADC + 0x14)
#define ADC_CHSELR (ADC + 0x28)
#define ADC_DR (ADC + 0x40)
#define ADC_CCR (ADC + 0x308)
#define CCR_TS ((1u << 23) | (1u << 22))

#ifdef REFCAL_KNOWN
static const int32_t KNOWN[] = {REFCAL_KNOWN}; /* pairs: kHz x 100, 0.1 Hz */
#endif

/* v in tenths: 16926.3 */
static char *du(char *p, uint32_t v) {
  uint32_t q = udiv(v, 10);
  p = dec(p, q);
  *p++ = '.';
  *p++ = (char)('0' + v - 10 * q);
  return p;
}

/* v in tenths, signed, with its sign: +539.8 */
static char *dt(char *p, int32_t v) {
  *p++ = v < 0 ? '-' : '+';
  return du(p, (uint32_t)(v < 0 ? -v : v));
}

static char *sgn(char *p, int v) {
  if (v >= 0) *p++ = '+';
  return sdec(p, v);
}

/* hz as kHz with two decimals: 470309.44 */
static char *khz(char *p, uint32_t hz) {
  uint32_t k = udiv(hz, 10), q = udiv(k, 100), r = k - 100 * q;
  p = dec(p, q);
  *p++ = '.';
  *p++ = (char)('0' + udiv(r, 10));
  *p++ = (char)('0' + r - 10 * udiv(r, 10));
  return p;
}

/* reference i: the ATSC pilots on RF 14-36 (the 10 Hz raster under edge + 309440.559 Hz),
   then the weather channels */
static uint32_t ref_hz(int i) {
#ifdef REFCAL_HZ
  (void)i;
  return REFCAL_HZ;
#else
  return i < 23 ? 470309440u + 6000000u * (uint32_t)i : 162400000u + 25000u * (uint32_t)(i - 23);
#endif
}

#ifdef REFCAL_HZ
#define NSCAN 1
#else
#define NSCAN NREFS
#endif

/* what the reference is off its nominal, 0.1 Hz: the ATSC raster's 0.56 Hz, and the Pluto's */
static int32_t ref_off(uint32_t hz) {
  uint32_t d = hz - 470309440u;
  int32_t o = hz >= 470309440u && d == 6000000u * udiv(d, 6000000u) ? 6 : 0;
#ifdef REFCAL_KNOWN
  unsigned j;
  for (j = 0; j + 1 < sizeof KNOWN / sizeof KNOWN[0]; j += 2)
    if ((uint32_t)KNOWN[j] == udiv(hz, 10)) o += KNOWN[j + 1];
#endif
  return o;
}

static int is_wx(uint32_t hz) { return hz < 170000000u; }

/* sixteen ADC reads of channel ch, summed (0: the ADC isn't on, or a read never ended) */
static uint32_t adc16(uint32_t ch) {
  uint32_t s = 0, i, n;
  wr(ADC_CHSELR, 1u << ch);
  for (i = 0; i < 16; i++) {
    wr(ADC_CR, rd(ADC_CR) | 4u);
    for (n = 0; !(rd(ADC_ISR) & 4u); n++)
      if (n > 20000) return 0;
    s += rd(ADC_DR) & 0xFFFu;
  }
  return s;
}

/* the temperature sensor's and VREFINT's sums; 0, 0 if Aura left the ADC off */
static void temp(const k6_api_t *api, uint32_t *t, uint32_t *v) {
  uint32_t ccr, sel, smp;
  *t = *v = 0;
  if (!(rd(ADC_CR) & 1u) || (rd(ADC_CR) & 4u)) return;   /* off, or a conversion running */
  ccr = rd(ADC_CCR), sel = rd(ADC_CHSELR), smp = rd(ADC_SMPR);
  wr(ADC_CCR, ccr | CCR_TS);
  wr(ADC_SMPR, 7);                                        /* the longest sample: the sensor is slow */
  api->delay_us(200);
  *t = adc16(16);
  *v = adc16(17);
  wr(ADC_SMPR, smp);
  wr(ADC_CHSELR, sel);
  wr(ADC_CCR, ccr);
  (void)rd(ADC_DR);
}

typedef struct {
  const k6_api_t *api;
  uint8_t *ring;
  uint32_t rsz;
  uint16_t r3b, x3c;
  int wide;
  char line[64];
} rc_t;

/* the receiver on hz, c->wide, REG_3B at base + k (taken at the RX on the tune does) */
static void at(rc_t *c, uint32_t hz, int k) {
  c->api->fd_write(0x3B, (uint16_t)(c->r3b + k));
  c->api->tune(hz, c->wide);
}

static int level(const k6_api_t *api) {
  int s = 0, i;
  for (i = 0; i < 4; i++) {
    api->delay_us(4000);
    s += rssi(api);
  }
  return s >> 2;
}

/* the mean IF over nb blocks, 0.1 Hz (0: too few good blocks, or a key) */
static int32_t mean_if(rc_t *c, uint32_t hz, int k, int nb) {
  blk_t blk;
  uint32_t sum = 0, lo = 0xFFFFFFFFu, hi = 0, n = 0, nl = 0, mn = 0xFFFFFFFFu, mx = 0;
  int i;
  char *p;
  c->api->delay_us(300000);                               /* the synthesizer and the IF settled */
  blk.ifm = 17000;
  if (is_wx(hz)) nb *= 2;                                 /* FM voice: average longer */
  for (i = 0; i < nb; i++) {
    uint32_t lost = run(c->ring, c->rsz, 64, 1, &blk, (int)blk.ifm);
    if (c->api->key_poll()) return 0;
    /* (+-5 kHz of the nominal IF: 8 ppm at 600 MHz before the first null; ifcal's +-2 kHz is
       3 ppm there, and the garage's cold may take the K6 further) */
    nl += lost != 0;
    if (!lost && blk.ifm < mn) mn = blk.ifm;
    if (!lost && blk.ifm > mx) mx = blk.ifm;
    if (lost || blk.ifm < 12000 || blk.ifm > 22000) continue;
    sum += blk.ifm, n++;
    if (blk.ifm < lo) lo = blk.ifm;
    if (blk.ifm > hi) hi = blk.ifm;
  }
  if (n < (uint32_t)nb / 2) {
    /* (what it saw: good blocks, blocks with losses, the IF range of the rest, Hz) */
    p = khz(put(c->line, TAG " m "), hz);
    p = kv(put(p, " few"), " n=", n);
    p = kv(p, " lost=", nl);
    p = kv(p, " lo=", mn > mx ? 0 : mn);
    say0(c->api, c->line, kv(p, " hi=", mx));
    return 0;
  }
  sum = udiv(sum * 10 + n / 2, n);
  p = khz(put(c->line, TAG " m "), hz);
  p = put(p, " if=");
  p = du(p, sum);
  p = kv(p, " n=", n);
  p = put(p, " sp=");
  p = du(p, (hi - lo) * 10);
  p = put(p, " k=");
  say0(c->api, c->line, sgn(p, k));
  return (int32_t)sum;
}

/* one step of REG_3B's move of the IF at hz, mHz: hz x 5 Hz / the crystal REG_3B / 3C say */
static uint32_t step_mhz(rc_t *c, uint32_t hz) {
  uint32_t x = (((uint32_t)(c->x3c >> 8) << 16) + c->r3b) * 5u;
  return udiv(udiv(hz, 1000) * 5000u, udiv(x, 1000));
}

/* round(a / b) for b > 0 */
static int32_t rdiv(int32_t a, uint32_t b) {
  uint32_t q = udiv((uint32_t)(a < 0 ? -a : a) + b / 2, b);
  return a < 0 ? -(int32_t)q : (int32_t)q;
}

#ifdef REFCAL_WAIT
/* up to 120 s for a carrier 15 dB over the floor that stays on REFCAL_HOLD_MS without a break:
   the tail, not the Pluto's MCW ID that k6_upload sends some 26 s after the upload (2026-11-18:
   gp0dma's carrier() fired on that ID, and the scan then found the gap before the tail) */
static int steady(const k6_api_t *api, int floor) {
  uint32_t t0 = api->ms(), on = 0, since = 0;
  api->say(TAG " waiting for a steady carrier");
  for (;;) {
    uint32_t now = api->ms();
    if (api->key_down()) return -2;
    if (now - t0 > 120000) {
      api->say(TAG " no steady carrier");
      return -6;
    }
    if (rssi(api) >= floor + 15) {
      if (!on) on = 1, since = now;
      if (now - since >= REFCAL_HOLD_MS) return 0;
    } else {
      on = 0;
    }
    api->delay_us(5000);
  }
}
#endif

int k6_main(const k6_api_t *api) {
  saved_t sv;
  rc_t c;
  uint32_t t, v, g, rx = api->rx_hz();
  int32_t m0, m1, d, e, ppb[REFCAL_NREF], x;
  int8_t lvl[NREFS], side[NREFS];
  int ret, i, j, best = -1, k = 0, sign = 0, n = 0, bad = 0, miss = 0, w0;
  uint16_t r73;
  char *p;
  (void)carrier;                                           /* (gp0dma's: refcal scans or steady()) */
  if (api->level < 4) return -3;
  c.api = api;
  ret = setup(api, &sv, &c.ring);
  if (ret) return ret;
  c.rsz = sv.rsz;
  c.r3b = api->fd_read(0x3B);
  c.x3c = api->fd_read(0x3C);
  r73 = api->fd_read(0x73);
  w0 = api->fd_read(0x43) == 0x3028;                       /* (ifcal's test for wide) */
  c.wide = 0;                                              /* the scan: narrow */
#ifdef REFCAL_WAIT
  ret = steady(api, sv.floor);                             /* the tail's own key-up */
  if (ret) goto out;
  for (i = 0; i < REFCAL_SETTLE_MS / 100; i++) {
    if (api->key_poll()) {
      ret = -2;
      goto out;
    }
    api->delay_us(100000);
  }
#endif
  temp(api, &t, &v);
  p = kv(c.line, TAG " t=", t);
  p = kv(p, " v=", v);
  p = hex(put(p, " 3b="), c.r3b, 4);
  say0(api, c.line, hex(put(p, " 3c="), c.x3c, 4));

  /* the scan */
  for (i = 0; i < NSCAN; i++) {
    uint32_t hz = ref_hz(i);
    int a, b;
    at(&c, hz, 0);
    a = level(api);
    at(&c, hz - SIDE_HZ, 0);
    b = level(api);
    at(&c, hz + SIDE_HZ, 0);
    j = level(api);
    if (j < b) b = j;
    lvl[i] = (int8_t)(a < -127 ? -127 : a), side[i] = (int8_t)(b < -127 ? -127 : b);
    p = khz(put(c.line, TAG " s "), hz);
    *p++ = ' ';
    p = sdec(p, a);
    *p++ = ' ';
    say0(api, c.line, sdec(p, b));
    if (api->key_poll()) {
      ret = -2;
      goto out;
    }
  }
#ifdef REFCAL_SCAN_ONLY
  ret = 0;
  goto out;
#endif
  api->fd_write(0x73, (uint16_t)(r73 | 0x0010));          /* AFC off (taken at the next RX on) */
  /* the IF reads with the wide filter, where the nominal IF is known (26 MHz / 1536: ifcal,
     xtalstep): with the narrow one the relay's carrier read 8.9-9.8 kHz (2026-11-17 06:10Z) */
#ifndef REFCAL_NARROW
  c.wide = 1;
#endif
  while (n < REFCAL_NREF) {
    uint32_t hz;
    int32_t r;
    /* the strongest reference not yet read, REFCAL_SNR dB over its side */
    for (best = -1, i = 0; i < NSCAN; i++)
      if (lvl[i] - side[i] >= REFCAL_SNR && (best < 0 || lvl[i] - side[i] > lvl[best] - side[best]))
        best = i;
    if (best < 0) break;
#ifndef REFCAL_HZ
    lvl[best] = side[best];                                /* (read) */
#endif
    hz = ref_hz(best);
    g = step_mhz(&c, hz);
    if (!sign) {
      /* the first: the probe tells which way a step turns the IF here */
      at(&c, hz, 0);
      m0 = mean_if(&c, hz, 0, REFCAL_BLOCKS);
      at(&c, hz, REFCAL_PROBE);
      m1 = mean_if(&c, hz, REFCAL_PROBE, REFCAL_BLOCKS / 2);
      if (!m0 || !m1) {
        ret = api->key_poll() ? -2 : -6;
        goto out;
      }
      d = m1 - m0;                                         /* 0.1 Hz */
      p = khz(put(c.line, TAG " step "), hz);
      p = dt(put(p, " d="), d);
      say0(api, c.line, dt(put(p, " g="), (int32_t)udiv(g * REFCAL_PROBE + 50, 100)));
      x = d < 0 ? -d : d;                                  /* |d| x 100 vs PROBE g (mHz) */
      if ((uint32_t)x * 100u * 10u < 4u * REFCAL_PROBE * g || (uint32_t)x * 100u * 10u > 16u * REFCAL_PROBE * g) {
        /* not this one's carrier the IF follows (a TV pilot under its own data, 2026-11-17):
           the next strongest, up to REFCAL_PROBES of them */
        api->say(TAG " the probe moved the IF wrongly");
        if (++bad >= REFCAL_PROBES) break;
        continue;
      }
      sign = d < 0 ? -1 : 1;
      /* null-seeking from the read at the old REG_3B */
      r = m0 - NOM_DHZ;
      for (j = 0; j < 3; j++) {
        int dk = rdiv(-r * 100, g) * sign;
        if (!dk && j) break;
        k += dk;
        if (k > KMAX) k = KMAX;
        if (k < -KMAX) k = -KMAX;
        at(&c, hz, k);
        r = mean_if(&c, hz, k, REFCAL_BLOCKS);
        if (!r) {
          ret = api->key_poll() ? -2 : -6;
          goto out;
        }
        r -= NOM_DHZ;
        if ((uint32_t)(r < 0 ? -r : r) * 100u < g / 2) break;   /* within half a step */
      }
    } else {
      /* the next: one read at the null */
      at(&c, hz, k);
      r = mean_if(&c, hz, k, REFCAL_BLOCKS);
      if (!r) {
        if (api->key_poll()) {
          ret = -2;
          goto out;
        }
        if (++miss >= REFCAL_NREF) break;                  /* (REFCAL_HZ: the same one again) */
        continue;
      }
      r -= NOM_DHZ;
    }
    /* the crystal's error, as Hz of the LO at hz (0.1 Hz; + fast): k steps of g, each taking the
       LO down g, and what the residual says is left (the IF moves sign x the carrier's offset
       from the LO), less the reference's own offset from where it was tuned; then ppb (0.1 Hz x
       1e8 / hz = x 1e5 / (hz / 1000)) */
    e = rdiv(k * (int32_t)g, 100) + ref_off(hz) - sign * r;
    {
      uint32_t a = (uint32_t)(e < 0 ? -e : e), kh = udiv(hz, 1000);
      x = (int32_t)udiv(a * 100000u + kh / 2, kh);
      ppb[n] = e < 0 ? -x : x;
    }
    p = khz(put(c.line, TAG " r "), hz);
    p = sgn(put(p, " ppb="), ppb[n]);
    p = dt(put(p, " res="), r);
    say0(api, c.line, sgn(put(p, " k="), k));
    n++;
  }
  if (!n) {
    api->say(bad ? TAG " no reference passed the probe: nothing kept" : TAG " no reference");
    ret = bad ? -12 : -6;
    goto out;
  }
  /* the median (of an even count, the lower middle) */
  for (i = 0; i < n; i++)
    for (j = i + 1; j < n; j++)
      if (ppb[j] < ppb[i]) x = ppb[i], ppb[i] = ppb[j], ppb[j] = x;
  ret = (int)ppb[(n - 1) >> 1];
  p = sgn(put(c.line, TAG " ppb="), ret);
  p = kv(p, " n=", (uint32_t)n);
  p = sgn(put(p, " k="), k);
  p = kv(p, " t=", t);
#ifdef REFCAL_APPLY
  say0(api, c.line, put(p, " set"));
#else
  say0(api, c.line, put(p, " dry"));
  k = 0;
#endif
out:
  api->fd_write(0x73, r73);
  c.wide = w0;                                             /* the pager's own filter */
  if (ret < 0 && ret > -20) k = 0;                         /* (an error keeps nothing) */
  at(&c, rx, k);                                           /* back on the pager's frequency */
  teardown(api, &sv);
  return ret;
}
