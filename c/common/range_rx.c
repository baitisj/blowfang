/* PHY R receiver on audio (range_rx.h). SPDX-License-Identifier: MIT */
#include "range_rx.h"

#define RR_MASK (RR_HIST - 1u)
#ifndef RR_HYST
#define RR_HYST 16              /* a phase move needs a neighbour's E 1/RR_HYST over the current's */
#endif
#ifndef RR_EAVG
#define RR_EAVG 16              /* E's running mean: 1/RR_EAVG of each step */
#endif
#define Q_MAX 7
#ifndef RR_TRK_G
#define RR_TRK_G 4              /* the phase follower moves 1/2^RR_TRK_G of its error a bit */
#endif
#define RR_HM 15                /* the quadrature filter's half length (taps hs apart) */
#define RR_XMASK (RR_XR - 1u)

#if RR_TRACK
/* the Hilbert filter's taps at 1, 3 .. 15 (2 / (pi k), Hamming windowed, Q13): within 1% and a
   tenth of a degree from 450 to 3000 Hz at 8 kHz */
static const int16_t hq[8] = {5163, 1586, 803, 438, 231, 110, 48, 28};

/* a c / 2^14 for |c| <= 2^15 and |a| < 2^20 (y to 48 kHz), c to 1/1024 */
static int32_t mul14(int32_t a, int32_t c) {
    return a * (c >> 4) >> 10;
}
#endif

void rr_init(rr_state *r, unsigned fs) {
    unsigned char *p = (unsigned char *)r;
    for (unsigned i = 0; i < sizeof *r; i++)    /* no memset: a module has no library */
        p[i] = 0;
    r->T = ((uint32_t)fs << 16) / 1200u;       /* 32-bit: a module has no 64-bit divide */
#if RR_TRACK
    r->hs = (fs + 4000u) / 8000u;
    while (r->hs > 1u && 2u * RR_HM * r->hs >= RR_XR)
        r->hs--;
    r->rc = 1 << 14;
#endif
    /* the first step's last phase needs 2 bits of audio: start it a bit and a half in */
    r->base = r->T + r->T / 2u;
}

/* the sum of the audio from its start to t (Q16 samples), the sample at floor(t) counted for the
   fraction of it before t; needs floor(t) + 1 <= n and floor(t) > n - RR_HIST */
static uint32_t at(const uint32_t *cum, uint32_t t) {
    uint32_t i = t >> 16, f = (t & 0xFFFFu) >> 4;      /* Q12 */
    uint32_t c0 = cum[i & RR_MASK], c1 = cum[(i + 1u) & RR_MASK];
    int32_t x = (int32_t)(c1 - c0);
    return c0 + (uint32_t)((x * (int32_t)f) >> 12);
}

/* the integrate and dump on the edge at e, h either side */
static int32_t dump(const uint32_t *cum, uint32_t e, uint32_t h) {
    return (int32_t)(2u * at(cum, e) - at(cum, e - h) - at(cum, e + h));
}

/* the soft value's y at e: the audio turned back by the follower's angle; *yd: the turned
   quadrature's (where y would go if the angle grew) */
static int32_t y_at(const rr_state *r, uint32_t e, uint32_t h, int32_t *yd) {
    int32_t yi = dump(r->cum, e, h);
#if RR_TRACK
    int32_t yq = dump(r->cumq, e, h);
    if (yd)
        *yd = mul14(yq, r->rc) - mul14(yi, r->rs);
    return mul14(yi, r->rc) + mul14(yq, r->rs);
#else
    if (yd)
        *yd = 0;
    return yi;
#endif
}

static int32_t clamp_q(int32_t v) {
    return v > Q_MAX ? Q_MAX : v < -Q_MAX ? -Q_MAX : v;
}

static unsigned parity(unsigned v) {
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return v & 1u;
}

/* the sync's line bit j (MSB first) as the soft value's sign: bit 0 +1, bit 1 -1 */
static int ref(unsigned j) {
    return (RR_SYNC >> (31u - j)) & 1u ? -1 : 1;
}

static int correlate(const rr_state *r) {
    int c = 0;
    for (unsigned j = 0; j < 32; j++)
        c += ref(j) * r->win[(r->wpos + j) & 31u];   /* wpos: the oldest */
    return c;
}

/* the packet's soft value i (4 bits a value, two a byte) */
static int soft_at(const rr_state *r, unsigned i) {
    unsigned v = (r->soft[i >> 1] >> ((i & 1u) * 4u)) & 15u;
    return (int)v - (int)((v & 8u) << 1);
}

/* one soft value into the bit layer: 1 when a packet's last one came */
#ifdef RR_TRACE
void rr_trace(const rr_state *r, int q);
#endif
static int bit_in(rr_state *r, int8_t q) {
#ifdef RR_TRACE
    rr_trace(r, q);
#endif
    r->win[r->wpos] = q;
    r->wpos = (uint8_t)((r->wpos + 1u) & 31u);
    r->bits++;
    int c = correlate(r), ac = c < 0 ? -c : c;
    if (r->collecting) {
        /* a better sync in the packet's first bits, or a strong one anywhere in it (a false sync
           in noise must not swallow the packet that follows): the packet starts here */
        if ((r->got < RR_RESYNC_BITS && ac >= r->best + RR_RESYNC_MARGIN) ||
            (ac >= RR_SYNC_STRONG && ac > r->best)) {
            r->got = 0;
            r->best = (int16_t)ac;
            r->corr = (int16_t)c;
            r->sgn = c < 0 ? -1 : 1;
            r->start = r->bits;
            return 0;
        }
        unsigned sh = (r->got & 1u) * 4u;
        r->soft[r->got >> 1] = (uint8_t)((r->soft[r->got >> 1] & ~(15u << sh)) |
                                         (((unsigned)(r->sgn * q) & 15u) << sh));
        r->got++;
        if (r->got < RR_CODED)
            return 0;
        r->collecting = 0;
        return 1;
    }
    if (ac >= RR_SYNC_MIN) {
        r->collecting = 1;
        r->got = 0;
        r->best = (int16_t)ac;
        r->corr = (int16_t)c;
        r->sgn = c < 0 ? -1 : 1;
        r->start = r->bits;
    }
    return 0;
}

/* this step's edges at all phases (their audio is in): the clock, then the soft values out */
static int step(rr_state *r) {
    uint32_t h = r->T / 2u;
    int32_t ebest = -1;
    unsigned best = 0, p, old = r->ph;
    int done = 0;
    for (p = 0; p < RR_NPH; p++) {
        int32_t y = y_at(r, r->base + r->T * p / RR_NPH, h, 0);
        int32_t a = (y < 0 ? -y : y) << 4;
        r->E[p] += (a - r->E[p]) / RR_EAVG;
        if (r->E[p] > ebest)
            ebest = r->E[p], best = p;
    }
    if (r->warm < 255)
        r->warm++;
    /* while the bank warms up, or when another phase is far better (a burst starting, or a
       lock half a bit off), straight to the best phase */
    if (r->warm < 64 || ebest > r->E[old] + r->E[old] / 2) {
        if (best != old)
            r->ph = (uint8_t)best;
    } else {
        /* otherwise a step to a neighbour at most (a drift is far slower): the bank mustn't hop
           between peaks and count a bit twice or not at all (11-13 K11: the K6's clock wandering
           against its tone made it do that) */
        unsigned up = (old + 1u) % RR_NPH, dn = (old + RR_NPH - 1u) % RR_NPH;
        unsigned nb = r->E[up] > r->E[dn] ? up : dn;
        if (r->E[nb] > r->E[old] + r->E[old] / RR_HYST)
            r->ph = (uint8_t)nb;
    }
    int d = (int)r->ph - (int)old;
    unsigned nout = 1;
    if (d < -(int)(RR_NPH / 2))
        nout = 0;                   /* the edges moved later past a wrap: this one was said */
    else if (d > (int)(RR_NPH / 2))
        nout = 2;                   /* earlier past a wrap: the step before's edge at the new
                                       phase (a bit after the old one's), then this step's */
    /* between phases: the top of a parabola through E at ph - 1, ph, ph + 1 (1/256 phase, at
       most one phase either way), where this step's soft value is taken. The bank only says
       which phase; a drift between phase moves costs nothing (11-13 run 5: 4 ppm left the
       soft values up to 1/16 bit early by a burst's end, and the errors climbed) */
    int32_t em = r->E[(r->ph + RR_NPH - 1u) % RR_NPH], e0 = r->E[r->ph];
    int32_t ep = r->E[(r->ph + 1u) % RR_NPH], den = em - 2 * e0 + ep, off = 0;
    if (den < 0) {
        /* 128 (em - ep) / den in 32 bits (a module has no 64-bit divide): past 2 |den| it clamps
           anyway; otherwise scale both under 2^23 first */
        int32_t a = em - ep, nd = -den;
        if (a >= 2 * nd || -a >= 2 * nd)
            off = a > 0 ? -256 : 256;
        else {
            while (nd > (1 << 22)) {
                a /= 2;
                nd /= 2;
            }
            off = nd ? -(a * 128) / nd : 0;
        }
    }
    int32_t yd;
    int32_t yf = y_at(r, r->base + r->T * r->ph / RR_NPH + (uint32_t)((int32_t)(r->T / RR_NPH) * off / 256),
                      h, &yd);
    int32_t amp = r->E[r->ph] >> 4;
#if RR_TRACK
    /* the follower: turn the angle by (the soft value's sign) yd / amp (about the angle's error
       in radians, clean), 1/2^RR_TRK_G of it, then back to length 1 (one Newton step) */
    if (amp > 0) {
        int32_t er = yd * 256 / amp;                     /* Q8 */
        er = er > 256 ? 256 : er < -256 ? -256 : er;
        int32_t da = (yf < 0 ? -er : er) * 64 / (1 << RR_TRK_G);   /* Q14 radians */
        int32_t c = r->rc - mul14(r->rs, da), sn = r->rs + mul14(r->rc, da);
        int32_t m = (c * c + sn * sn) >> 14;
        r->rc = (c * (3 * (1 << 14) - m)) >> 15;
        r->rs = (sn * (3 * (1 << 14) - m)) >> 15;
    }
#endif
    for (unsigned k = 0; k < nout; k++) {
        /* the step before's edge at this phase: its audio is still in (3 bits back) */
        int32_t y = nout == 2 && k == 0 ? y_at(r, r->base - r->T + r->T * r->ph / RR_NPH, h, 0) : yf;
        int32_t q = amp > 0 ? clamp_q((4 * y + (y < 0 ? -amp / 2 : amp / 2)) / amp) : 0;
        if (bit_in(r, (int8_t)q))
            done = 1;               /* a pair's second soft value starts the hunt again */
    }
    r->base += r->T;
    return done;
}

int rr_push(rr_state *r, const int16_t *x, unsigned n, unsigned *used) {
    for (unsigned i = 0; i < n; i++) {
        int32_t v = (int32_t)x[i] * 256 - r->dc;
        r->dc += v / 1024;          /* a high-pass near fs / 6400 (1.2 Hz at 8 kHz) */
#if RR_TRACK
        v /= 256;
        r->xr[r->nin & RR_XMASK] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
        r->nin++;
        /* the receiver's sample m: RR_HM taps behind (its quadrature needs the audio after it);
           the ring before the first sample is zeros */
        uint32_t hs = r->hs, d = RR_HM * hs;
        if (r->nin <= d)
            continue;
        uint32_t m = r->nin - 1u - d;
        int32_t q = 0;
        for (unsigned k = 0; k < 8u; k++) {
            uint32_t o = (2u * k + 1u) * hs;
            q += hq[k] * (r->xr[(m - o) & RR_XMASK] - r->xr[(m + o) & RR_XMASK]);
        }
        r->cumq[(r->n + 1u) & RR_MASK] = r->cumq[r->n & RR_MASK] + (uint32_t)(q >> 13);
        r->cum[(r->n + 1u) & RR_MASK] = r->cum[r->n & RR_MASK] + (uint32_t)(int32_t)r->xr[m & RR_XMASK];
#else
        r->cum[(r->n + 1u) & RR_MASK] = r->cum[r->n & RR_MASK] + (uint32_t)(v / 256);
#endif
        r->n++;
        /* this step's last phase, one more (the soft value's fine timing) and the half bit
           after it are in */
        uint32_t last = r->base + r->T + r->T / 2u;
        /* sample counts mod 2^16, as base's whole part (RR_HIST divides 2^16) */
        if ((int16_t)(uint16_t)((last >> 16) + 1u - r->n) <= 0 && step(r)) {
            rr_decode(r);
            *used = i + 1;
            return 1;
        }
    }
    *used = n;
    return 0;
}

unsigned rr_seq(const uint8_t *p) {
    unsigned s = 0x1FF, b[4];
    for (unsigned i = 0; i < 4; i++) {
        b[i] = p[i] ^ (s & 0xFFu);
        for (int k = 0; k < 8; k++)
            s = (s >> 1) | (((s ^ (s >> 5)) & 1u) << 8);
    }
    return (b[2] << 8 | b[3]) & 0x1FFFu;
}

int rr_crc_ok(const uint8_t *p) {
    unsigned s = 0x1FF, crc = 0xFFFF, i, want = 0;
    for (i = 0; i < RR_PAYLOAD; i++) {
        unsigned b = p[i] ^ (s & 0xFFu);
        for (int k = 0; k < 8; k++)
            s = (s >> 1) | (((s ^ (s >> 5)) & 1u) << 8);
        if (i < RR_PAYLOAD - 2u) {
            crc ^= b << 8;
            for (int k = 0; k < 8; k++)
                crc = (crc & 0x8000u) ? ((crc << 1) ^ 0x1021u) & 0xFFFFu : (crc << 1) & 0xFFFFu;
        } else {
            want = want << 8 | b;
        }
    }
    return crc == want;
}

/* line position of coded bit j: line bit i is coded bit 86 (i % 24) + i / 24 */
static unsigned line_of(unsigned j) {
    return (j % 86u) * 24u + j / 86u;
}

void rr_decode(rr_state *r) {
    int32_t pm[2][64];
    unsigned s, t, cur = 0;
    for (s = 0; s < 64; s++)
        pm[0][s] = s ? -100000 : 0;
    for (t = 0; t < RR_STEPS; t++) {
        int s0 = soft_at(r, line_of(2 * t)), s1 = soft_at(r, line_of(2 * t + 1));
        uint32_t w0 = 0, w1 = 0;
        for (unsigned ns = 0; ns < 64; ns++) {
            int32_t m[2];
            for (unsigned b = 0; b < 2; b++) {
                unsigned reg = (b << 6) | ns, ps = (ns >> 1) | (b << 5);
                int bm = (parity(reg & 0133u) ? -s0 : s0) + (parity(reg & 0171u) ? -s1 : s1);
                m[b] = pm[cur][ps] + bm;
            }
            unsigned pick = m[1] > m[0];
            pm[cur ^ 1u][ns] = m[pick];
            if (pick) {
                if (ns < 32)
                    w0 |= 1u << ns;
                else
                    w1 |= 1u << (ns - 32u);
            }
        }
        r->surv[t][0] = w0;
        r->surv[t][1] = w1;
        cur ^= 1u;
    }
    /* trace back from state 0 (the tail's zeros) */
    for (s = 0; s < RR_PAYLOAD; s++)
        r->payload[s] = 0;
    s = 0;
    for (t = RR_STEPS; t-- > 0;) {
        unsigned b = s & 1u, m = (s < 32 ? r->surv[t][0] >> s : r->surv[t][1] >> (s - 32u)) & 1u;
        if (t < RR_PAYLOAD * 8u && b)
            r->payload[t >> 3] |= (uint8_t)(0x80u >> (t & 7u));
        s = (s >> 1) | (m << 5);
    }
    /* the channel's errors: code the payload again, against the soft values' signs */
    unsigned reg = 0, err = 0, sum = 0;
    for (t = 0; t < RR_STEPS; t++) {
        unsigned bit = t < RR_PAYLOAD * 8u ? (r->payload[t >> 3] >> (7u - (t & 7u))) & 1u : 0u;
        reg = ((reg << 1) | bit) & 127u;
        for (unsigned k = 0; k < 2; k++) {
            unsigned c = parity(reg & (k ? 0171u : 0133u));
            int v = soft_at(r, line_of(2 * t + k));
            err += (v > 0 && c) || (v < 0 && !c) || v == 0;
            sum += (unsigned)(v < 0 ? -v : v);
        }
    }
    r->errors = (int)err;
    r->ymean = (int)(sum * 10u / RR_CODED);
    r->ok = rr_crc_ok(r->payload);
    r->pcorr = r->corr;
    r->psync = r->start;
}
