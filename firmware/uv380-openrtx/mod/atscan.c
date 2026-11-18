/* atscan: what does the UV-380's receiver hear across a range of UHF frequencies? A receive-only
   RSSI sweep (thread "Find K6 position without GPS", 11-18): Jeff asked whether loud trunked
   control channels (P25 at 851-869 MHz, outside the radio's 400-480 MHz) leak into the UV-380,
   for instance at half their frequency (a mixer working on its LO's second harmonic) or as
   images. The Pluto finds the control channels; this module looks where they could land.

   How: the AT1846S's frequency registers (0x29 high, 0x2A low; 1/16 kHz units, as OpenRTX's
   AT1846S::setFrequency writes them) are set for each point, the receiver is restarted by
   clearing and restoring register 0x30's rx bit (0x0020; OpenRTX's reloadConfig), and after
   SC_SETTLE_MS the RSSI (api->rssi, the AT1846S's own) is read SC_READS times, SC_GAP_MS apart:
   the point's mean and peak. Then the next point. At the end the old 0x29/0x2A/0x30 go back
   and the receiver restarts on its own channel.

   It only ever moves the receive frequency, inside SC_MIN_KHZ..SC_MAX_KHZ (the UHF path the
   radio is already on, so no band switch or front-end GPIO is touched). It never sets 0x30's
   tx bit (0x0040): if that bit is set when it starts, or at any point, it stops at once and
   puts everything back. PTT, preview or Ctrl-C (api->stop) end it the same way.

   Lines ("mod say ..."):
     scan start n=NNN lo=425000 hi=435000 step=12.5 f0=441850 r30=XXXX
     r 425000 ccccccc...        one row of up to 50 points from that frequency (kHz), one char a
                                point: '0' + (mean dBm + 160) / 2 ('0' = -160 dBm or less)
     p 425612 m=-98 fl=-121     a point SC_SIG_DB or more over the sweep's median: kHz,
                                its mean (dBm), the median
     t 425612 m=-98 x=-95 a=-119 b=-120   SC_TARGETS: each target, then its two neighbours
                                SC_NEAR_KHZ below (a=) and above (b=)
     scan end n=NNN ms=NNNNN ret=R
   Returns the points SC_SIG_DB or more over the median, -1 (I2C), -2 (tx bit set), -3 (stopped).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "at1846.h"

#ifndef SC_LO_KHZ
#define SC_LO_KHZ 425000u        /* (851..869 MHz) / 2: the 800 MHz trunking band at half */
#endif
#ifndef SC_HI_KHZ
#define SC_HI_KHZ 435000u
#endif
#ifndef SC_STEP_HZ
#define SC_STEP_HZ 12500u
#endif
#define SC_MIN_KHZ 400000u       /* the UHF path the radio is on: never leave it */
#define SC_MAX_KHZ 480000u
#define SC_SETTLE_MS 25u
#define SC_READS 4u
#define SC_GAP_MS 5u
#define SC_SIG_DB 8
#define SC_NEAR_KHZ 50u
#define SC_ROW 50u
#define SC_MAXN 1024u

/* targets (kHz): half of each control channel the Pluto found; filled in for each run */
static const uint32_t SC_TARGETS[] = {
    0u,
};

#define R30_RX 0x0020u
#define R30_TX 0x0040u
#define R30_RXTX 0x0060u

typedef struct {
    uint16_t r29, r2a, r30;
} saved_t;

static int tx_on(const ortx_api_t *api) {
    uint16_t v = 0;
    if (at_read(api, 0x30, &v))
        return 1;                /* can't tell: treat as unsafe */
    return (v & R30_TX) != 0;
}

/* 0, -1 (I2C), -2 (tx bit) */
static int tune(const ortx_api_t *api, const saved_t *s, uint32_t khz) {
    if (khz < SC_MIN_KHZ || khz > SC_MAX_KHZ)
        return -1;
    uint16_t r30 = 0;
    if (at_read(api, 0x30, &r30))
        return -1;
    if (r30 & R30_TX)
        return -2;
    uint32_t v = khz * 16u;
    if (at_write(api, 0x29, (uint16_t)(v >> 16)) || at_write(api, 0x2A, (uint16_t)v))
        return -1;
    if (at_write(api, 0x30, (uint16_t)(r30 & ~R30_RXTX)))
        return -1;
    api->sleep_ms(1);
    if (at_write(api, 0x30, (uint16_t)((r30 | (s->r30 & R30_RX)) & ~R30_TX)))
        return -1;
    return 0;
}

static void put_back(const ortx_api_t *api, const saved_t *s) {
    uint16_t r30 = 0;
    at_write(api, 0x29, s->r29);
    at_write(api, 0x2A, s->r2a);
    if (at_read(api, 0x30, &r30) == 0 && !(r30 & R30_TX)) {
        at_write(api, 0x30, (uint16_t)(r30 & ~R30_RXTX));
        api->sleep_ms(1);
        at_write(api, 0x30, (uint16_t)(s->r30 & ~R30_TX));
    }
}

/* mean and peak dBm at one point: 0, or the tune's error, or -3 (stopped) */
static int measure(const ortx_api_t *api, const saved_t *s, uint32_t hz, int32_t *mean, int32_t *peak) {
    int rc = tune(api, s, hz / 1000u);
    if (rc)
        return rc;
    api->sleep_ms(SC_SETTLE_MS);
    int32_t sum = 0, mx = -999;
    for (unsigned i = 0; i < SC_READS; i++) {
        int32_t r = api->rssi();
        sum += r;
        if (r > mx)
            mx = r;
        api->sleep_ms(SC_GAP_MS);
    }
    *mean = sum / (int32_t)SC_READS;
    *peak = mx;
    return api->stop() ? -3 : 0;
}

static char lvl(int32_t dbm) {
    int32_t c = (dbm + 160) / 2;
    if (c < 0)
        c = 0;
    if (c > 63)
        c = 63;
    return (char)('0' + c);
}

int uv_main(const ortx_api_t *api) {
    char line[200];
    uint32_t bytes = 0;
    int8_t *lv = (int8_t *)api->scratch(&bytes);       /* mean dBm + 40 per point (int8) */
    uint32_t step = SC_STEP_HZ;
    uint32_t n = (uint32_t)(((uint64_t)(SC_HI_KHZ - SC_LO_KHZ) * 1000u) / step) + 1u;
    if (n > SC_MAXN)
        n = SC_MAXN;
    if (lv == 0 || bytes < n)
        return -8;
    saved_t s;
    if (at_read(api, 0x29, &s.r29) || at_read(api, 0x2A, &s.r2a) || at_read(api, 0x30, &s.r30))
        return -1;
    uint32_t f0 = (((uint32_t)s.r29 << 16) | s.r2a) / 16u;
    api->fmt(line, sizeof line, "scan start n=%lu lo=%lu hi=%lu step=%lu f0=%lu r30=%04x", (unsigned long)n,
             (unsigned long)SC_LO_KHZ, (unsigned long)SC_HI_KHZ, (unsigned long)step, (unsigned long)f0,
             (unsigned)s.r30);
    api->say(line);
    if (s.r30 & R30_TX) {
        api->say("scan refused: tx bit set");
        return -2;
    }
    int ret = 0;
    uint32_t t0 = api->ms();
    char row[SC_ROW + 1];
    unsigned k = 0;
    uint32_t row_khz = SC_LO_KHZ;
    uint32_t done = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t hz = SC_LO_KHZ * 1000u + i * step;
        int32_t m = 0, x = 0;
        int rc = measure(api, &s, hz, &m, &x);
        if (rc) {
            ret = rc;
            break;
        }
        int32_t c = m + 40;
        lv[i] = (int8_t)(c < -127 ? -127 : c > 127 ? 127 : c);
        if (k == 0)
            row_khz = hz / 1000u;
        row[k++] = lvl(m);
        done = i + 1;
        if (k == SC_ROW || i + 1 == n) {
            row[k] = 0;
            put_back(api, &s);           /* the own channel between rows, so OpenRTX sees no gap */
            api->fmt(line, sizeof line, "r %lu %s", (unsigned long)row_khz, row);
            api->say(line);
            k = 0;
            if (tx_on(api)) {
                ret = -2;
                break;
            }
        }
    }
    /* the median over the points measured: a histogram of the int8 levels */
    int32_t med = -160;
    if (done) {
        uint16_t hist[64];
        for (unsigned b = 0; b < 64u; b++)
            hist[b] = 0;
        for (uint32_t i = 0; i < done; i++) {
            int32_t b = (lv[i] - 40 + 160) / 2;
            hist[b < 0 ? 0 : b > 63 ? 63 : b]++;
        }
        uint32_t acc = 0;
        for (unsigned b = 0; b < 64u; b++) {
            acc += hist[b];
            if (acc * 2u >= done) {
                med = (int32_t)b * 2 - 160;
                break;
            }
        }
    }
    int sig = 0;
    for (uint32_t i = 0; i < done && ret == 0; i++) {
        int32_t m = lv[i] - 40;
        if (m - med >= SC_SIG_DB) {
            sig++;
            api->fmt(line, sizeof line, "p %lu m=%ld fl=%ld", (unsigned long)(SC_LO_KHZ + (i * step) / 1000u),
                     (long)m, (long)med);
            api->say(line);
        }
    }
    for (unsigned t = 0; ret == 0 && t < sizeof SC_TARGETS / sizeof SC_TARGETS[0]; t++) {
        uint32_t f = SC_TARGETS[t];
        if (f == 0u)
            continue;
        int32_t m, x, a, ax, b, bx;
        int rc = measure(api, &s, f * 1000u, &m, &x);
        if (!rc)
            rc = measure(api, &s, (f - SC_NEAR_KHZ) * 1000u, &a, &ax);
        if (!rc)
            rc = measure(api, &s, (f + SC_NEAR_KHZ) * 1000u, &b, &bx);
        put_back(api, &s);
        if (rc) {
            ret = rc;
            break;
        }
        api->fmt(line, sizeof line, "t %lu m=%ld x=%ld a=%ld b=%ld", (unsigned long)f, (long)m, (long)x, (long)a,
                 (long)b);
        api->say(line);
    }
    put_back(api, &s);
    api->fmt(line, sizeof line, "scan end n=%lu ms=%lu ret=%d", (unsigned long)done,
             (unsigned long)(api->ms() - t0), ret ? ret : sig);
    api->say(line);
    return ret ? ret : sig;
}
