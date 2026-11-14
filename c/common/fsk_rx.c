/* fsk_rx: see fsk_rx.h. SPDX-License-Identifier: MIT */
#include "fsk_rx.h"

#include <string.h>

#include "pager_tx.h"

/* cos and sin of 2 pi 1500 n / 8000, Q14 */
static const int16_t fr_cos[16] = {16384, 6270, -11585, -15137, 0, 15137, 11585, -6270,
                                   -16384, -6270, 11585, 15137, 0, -15137, -11585, 6270};
static const int16_t fr_sin[16] = {0, 15137, 11585, -6270, -16384, -6270, 11585, 15137,
                                   0, -15137, -11585, 6270, 16384, 6270, -11585, -15137};
/* low-pass, 1.3 kHz at 8 kHz, Q14 (scipy firwin(11, 1300, fs=8000)): -57 dB at 2.7 kHz, where
   the mixer's image of the tones lands */
static const int16_t fr_h[FR_TAPS] = {-78, -179, 55, 1602, 4100, 5383, 4100, 1602, 55, -179, -78};

#define FR_T ((uint32_t)(((uint64_t)FR_FS << 16) / FR_BAUD))   /* samples a bit, Q16 */

void fr_init(fr_state *r) {
    memset(r, 0, sizeof(*r));
    for (unsigned p = 0; p < FR_NPH; p++)
        r->next[p] = FR_T / FR_NPH * p + FR_T;
}

static unsigned popcount32(uint32_t v) {
    v = v - ((v >> 1) & 0x55555555u);
    v = (v & 0x33333333u) + ((v >> 2) & 0x33333333u);
    return (((v + (v >> 4)) & 0x0F0F0F0Fu) * 0x01010101u) >> 24;
}

static int crc_ok(const uint8_t *b, unsigned n) {
    uint8_t p[FR_MAX];
    memcpy(p, b, n);
    pt_whiten(p, n);
    return pt_crc16(p, n - 2u) == ((unsigned)p[n - 2] << 8 | p[n - 1]);
}

/* the packet ends: 1 */
static int done(fr_state *r, unsigned len, int ok) {
    memcpy(r->payload, r->buf, len);
    r->len = len;
    r->ok = ok;
    r->psync = r->spos;
    r->pe = r->e[r->cph];
    r->collecting = 0;
    return 1;
}

/* phase p's next soft value (positive: mark, bit 1): 1 when a packet ended */
static int bit_in(fr_state *r, unsigned p, int32_t soft) {
    int32_t a = soft < 0 ? -soft : soft;
    r->e[p] += (a - r->e[p]) >> 4;
    uint32_t pos = r->k[p] * FR_NPH + p;
    r->k[p]++;
    unsigned b = soft > 0;
    r->reg[p] = r->reg[p] << 1 | b;
    unsigned err = popcount32(r->reg[p] ^ FR_SYNC);
    if (err <= FR_SYNC_ERR) {
        /* a sync: a new packet; the same sync at a better phase within a bit; or a perfect one
           where the packet being taken began on a sync with errors (noise makes false ones) */
        int32_t since = (int32_t)(pos - r->spos);
        if (!r->collecting || (since < (int32_t)FR_NPH && r->got <= 1u && r->e[p] > r->e[r->cph]) ||
            (err == 0 && r->serr != 0 && since >= (int32_t)FR_NPH)) {
            r->collecting = 1;
            r->cph = p;
            r->cpos = pos;
            r->spos = pos;
            r->serr = err;
            r->got = 0;
            r->ted = 0;
            return 0;
        }
    }
    if (!r->collecting)
        return 0;
    if (p != r->cph || (int32_t)(pos - r->cpos) <= (int32_t)(FR_NPH / 2u))
        return 0;
    r->cpos = pos;
    if (b)
        r->buf[r->got >> 3] |= (uint8_t)(0x80u >> (r->got & 7u));
    else
        r->buf[r->got >> 3] &= (uint8_t)~(0x80u >> (r->got & 7u));
    r->got++;
    if (r->got == 512u && crc_ok(r->buf, 64u))
        return done(r, 64u, 1);
    if (r->got == 1024u)
        return done(r, 128u, crc_ok(r->buf, 128u));
    /* follow the clock: the edge between this bit and the last, read by the phase half a bit
       off, against the change */
    int32_t prev = r->last[p], mid = r->last[(p + FR_NPH / 2u) % FR_NPH];
    if ((prev > 0) != (soft > 0)) {
        r->ted += prev > soft ? mid : -mid;
        int32_t lim = FR_TED * r->e[p];
        if (r->ted > lim || r->ted < -lim) {
            r->cph = r->ted > 0 ? (p + 1u) % FR_NPH : (p + FR_NPH - 1u) % FR_NPH;
            r->ted = 0;
        }
    }
    return 0;
}

int fr_push(fr_state *r, const int16_t *x, unsigned n, unsigned *used) {
    for (unsigned i = 0; i < n; i++) {
        /* mix down by 1500 Hz and low-pass */
        int32_t s = x[i];
        r->mi[r->mpos] = (s * fr_cos[r->ph]) >> 14;
        r->mq[r->mpos] = -((s * fr_sin[r->ph]) >> 14);
        r->ph = (r->ph + 1u) & 15u;
        int32_t zi = 0, zq = 0;
        unsigned j = r->mpos;
        for (unsigned t = 0; t < FR_TAPS; t++) {
            zi += fr_h[t] * r->mi[j];
            zq += fr_h[t] * r->mq[j];
            j = j ? j - 1u : FR_TAPS - 1u;
        }
        r->mpos = r->mpos + 1u < FR_TAPS ? r->mpos + 1u : 0;
        zi >>= 14;
        zq >>= 14;
        /* the turn since the last sample: sin of the angle, Q12; mark negative */
        int64_t cr = (int64_t)r->pi * zq - (int64_t)r->pq * zi;
        int64_t pw = (int64_t)zi * zi + (int64_t)zq * zq;
        int32_t f = pw > 64 ? (int32_t)((cr * 4096) / pw) : 0;
        r->pi = zi;
        r->pq = zq;
        r->n++;
        r->cum[r->n & (FR_HIST - 1u)] = r->cum[(r->n - 1u) & (FR_HIST - 1u)] + (uint32_t)(-f);
        /* the clock bank: each phase whose bit ends here */
        uint32_t now = r->n << 16;
        for (unsigned p = 0; p < FR_NPH; p++) {
            if ((int32_t)(now - r->next[p]) < 0)
                continue;
            uint32_t e = r->next[p] >> 16, b = (r->next[p] - FR_T) >> 16;
            r->next[p] += FR_T;
            int32_t soft = (int32_t)(r->cum[e & (FR_HIST - 1u)] - r->cum[b & (FR_HIST - 1u)]);
            int pkt = bit_in(r, p, soft);
            r->last[p] = soft;
            if (pkt) {
                *used = i + 1u;
                return 1;
            }
        }
    }
    *used = n;
    return 0;
}
