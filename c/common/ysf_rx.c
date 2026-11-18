/* YSF receiver for 8 kHz discriminator audio (ysf_rx.h). SPDX-License-Identifier: MIT */
#include "ysf_rx.h"

/* RRC alpha 0.2, 5 samples a symbol, +-6 symbols, summing to 3 (the x3 interpolator's gain):
   modem.m17.rrc(0.2, 5, 6) * 3 */
static const float YSF_RRC[YSF_NTAP] = {
    0.00474392f, 0.00591298f, 0.00331124f, -0.00222658f, -0.00788993f, -0.01015261f, -0.00668638f,
    0.00202341f, 0.01231312f, 0.01866922f, 0.01636119f, 0.00422427f, -0.01385534f, -0.02979569f,
    -0.03455169f, -0.02256473f, 0.00455540f, 0.03709320f, 0.06005073f, 0.05915047f, 0.02786046f,
    -0.02754181f, -0.08745647f, -0.12400083f, -0.11038219f, -0.03142463f, 0.10876851f, 0.28530441f,
    0.45831223f, 0.58451425f, 0.63071970f, 0.58451425f, 0.45831223f, 0.28530441f, 0.10876851f,
    -0.03142463f, -0.11038219f, -0.12400083f, -0.08745647f, -0.02754181f, 0.02786046f, 0.05915047f,
    0.06005073f, 0.03709320f, 0.00455540f, -0.02256473f, -0.03455169f, -0.02979569f, -0.01385534f,
    0.00422427f, 0.01636119f, 0.01866922f, 0.01231312f, 0.00202341f, -0.00668638f, -0.01015261f,
    -0.00788993f, -0.00222658f, 0.00331124f, 0.00591298f, 0.00474392f};

static const uint8_t YSF_SYNCB[5] = {0xD4, 0x71, 0xC9, 0x63, 0x4D};

#define YSF_PAY_AT 240u                 /* frame bit of the payload: sync + FICH */
#define YSF_NU 280u                     /* sotto VW: five (48 + 8) info bits */
#define YSF_GOLAY_POLY 0xC75u

static unsigned ysf_parity(unsigned v) {
    v ^= v >> 16;
    v ^= v >> 8;
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return v & 1u;
}

static unsigned ysf_weight(uint32_t v) {
    unsigned n = 0;
    for (; v; v &= v - 1u)
        n++;
    return n;
}

static unsigned ysf_pn9(unsigned *s) {
    unsigned b = *s & 1u;
    *s = (*s >> 1) | ((((*s >> 4) ^ *s) & 1u) << 8);
    return b;
}

static float ysf_sync_level(unsigned k) {
    unsigned d = (YSF_SYNCB[k >> 2] >> (6u - 2u * (k & 3u))) & 3u;
    float v = (d & 1u) ? 3.0f : 1.0f;
    return (d & 2u) ? -v : v;
}

static float ysf_yat(const ysf_rx *r, uint32_t i) {
    return (float)r->y[i & (YSF_YN - 1u)];
}

/* the sync correlation of the 20 symbols from 24 kHz sample `start`, squared with the sign kept */
static float ysf_score(const ysf_rx *r, uint32_t start) {
    float c = 0.0f, e = 0.0f, pp = 0.0f;
    for (unsigned k = 0; k < YSF_NSYNC; k++) {
        float v = ysf_yat(r, start + YSF_SPS * k), p = ysf_sync_level(k);
        c += v * p;
        e += v * v;
        pp += p * p;
    }
    if (e <= 0.0f)
        return 0.0f;
    float s = c * c / (e * pp);
    return c < 0.0f ? -s : s;
}

/* soft value of frame bit b (positive = 0): MSB the level, LSB 2 - |level| */
static float ysf_bit(const ysf_rx *r, unsigned b) {
    float v = (float)r->sym[b >> 1] * r->inv;
    if (v > 4.0f)
        v = 4.0f;
    if (v < -4.0f)
        v = -4.0f;
    if (!(b & 1u))
        return v;
    return 2.0f - (v < 0.0f ? -v : v);
}

/* coded bit k of a K=5 field: 0 the FICH (5 x 20 dibits), 1 / 2 CSD1 / CSD2 (9 x 20 dibits, the
   first / last 72 bits of each 144-bit section) */
static float ysf_coded5(const ysf_rx *r, int field, unsigned k) {
    unsigned i = k >> 1;
    if (field == 0)
        return ysf_bit(r, 40u + (i % 5u) * 40u + (i / 5u) * 2u + (k & 1u));
    unsigned fb = (i % 9u) * 40u + (i / 9u) * 2u + (k & 1u);
    return ysf_bit(r, YSF_PAY_AT + 144u * (fb / 72u) + fb % 72u + (field == 2 ? 72u : 0u));
}

/* soft K=5 Viterbi (g1 = 1 + D^3 + D^4, g2 = 1 + D + D^2 + D^4: M17's code too) over n pairs,
   ending in state 0 (4 flush bits); nbits out MSB first; returns the share of soft weight the path
   disagrees with */
static float ysf_viterbi5(ysf_rx *r, int field, unsigned n, uint8_t *out, unsigned nbits) {
    float pm[16], nw[16], sum = 0.0f;
    uint16_t *surv = r->surv.k5;
    for (unsigned s = 0; s < 16u; s++)
        pm[s] = s ? 1e30f : 0.0f;
    for (unsigned k = 0; k < n; k++) {
        float a = ysf_coded5(r, field, 2u * k), c = ysf_coded5(r, field, 2u * k + 1u);
        sum += (a < 0.0f ? -a : a) + (c < 0.0f ? -c : c);
        uint16_t sv = 0;
        for (unsigned ns = 0; ns < 16u; ns++) {
            float best = 0.0f;
            unsigned bx = 0;
            for (unsigned x = 0; x < 2u; x++) {
                unsigned s = (ns >> 1) | (x << 3), m = ((s << 1) | (ns & 1u)) & 0x1Fu;
                float e1 = ysf_parity(m & 0x19u) ? -1.0f : 1.0f, e2 = ysf_parity(m & 0x17u) ? -1.0f : 1.0f;
                float v = pm[s] - a * e1 - c * e2;
                if (x == 0 || v < best) {
                    best = v;
                    bx = x;
                }
            }
            nw[ns] = best;
            sv |= (uint16_t)(bx << ns);
        }
        surv[k] = sv;
        for (unsigned s = 0; s < 16u; s++)
            pm[s] = nw[s];
    }
    float metric = sum > 0.0f ? (sum + pm[0]) / (2.0f * sum) : 1.0f;
    for (unsigned i = 0; i < (nbits + 7u) / 8u; i++)
        out[i] = 0;
    unsigned s = 0;
    for (unsigned k = n; k-- > 0;) {
        if (k < nbits && (s & 1u))
            out[k >> 3] |= (uint8_t)(0x80u >> (k & 7u));
        unsigned x = (surv[k] >> s) & 1u;
        s = (s >> 1) | (x << 3);
    }
    return metric;
}

static uint32_t ysf_golay24(unsigned d) {
    uint32_t rem = (uint32_t)d << 11;
    for (unsigned i = 22; i >= 11u; i--)
        if (rem & (1u << i))
            rem ^= (uint32_t)YSF_GOLAY_POLY << (i - 11u);
    uint32_t c = (uint32_t)d << 11 | rem;
    return c << 1 | ysf_parity(c);
}

/* 24-bit word -> 12 data bits within 3 errors, else -1: every data pattern of up to 3 flips */
static int ysf_golay(uint32_t cw) {
    unsigned d = (cw >> 12) & 0xFFFu;
    if (ysf_weight(cw ^ ysf_golay24(d)) <= 3u)
        return (int)d;
    for (unsigned a = 0; a < 12u; a++) {
        unsigned ea = 1u << a;
        if (ysf_weight(cw ^ ysf_golay24(d ^ ea)) <= 2u)
            return (int)(d ^ ea);
        for (unsigned b = a + 1u; b < 12u; b++) {
            unsigned eb = ea | 1u << b;
            if (ysf_weight(cw ^ ysf_golay24(d ^ eb)) <= 3u)
                return (int)(d ^ eb);
            for (unsigned c = b + 1u; c < 12u; c++) {
                unsigned ec = eb | 1u << c;
                if (ysf_weight(cw ^ ysf_golay24(d ^ ec)) <= 3u)
                    return (int)(d ^ ec);
            }
        }
    }
    return -1;
}

uint16_t ysf_crc(const uint8_t *d, unsigned n) {
    uint16_t crc = 0;
    for (unsigned i = 0; i < n; i++) {
        crc ^= (uint16_t)(d[i] << 8);
        for (unsigned b = 0; b < 8u; b++)
            crc = (uint16_t)((crc & 0x8000u) ? ((unsigned)crc << 1) ^ 0x1021u : (unsigned)crc << 1);
    }
    return (uint16_t)(crc ^ 0xFFFFu);
}

static uint8_t ysf_crc8(const uint8_t *d) {
    unsigned c = 0;
    for (unsigned i = 0; i < 6u; i++) {
        c ^= d[i];
        for (unsigned b = 0; b < 8u; b++)
            c = (c & 0x80u) ? ((c << 1) ^ 0x07u) & 0xFFu : (c << 1) & 0xFFu;
    }
    return (uint8_t)c;
}

/* the FICH: 0 and its four bytes, or -1 */
static int ysf_fich(ysf_rx *r, uint8_t out[4], float *metric) {
    uint8_t cw[13], raw[6];
    *metric = ysf_viterbi5(r, 0, 100, cw, 96);
    uint64_t word = 0;
    for (unsigned g = 0; g < 4u; g++) {
        uint32_t w = (uint32_t)cw[3 * g] << 16 | (uint32_t)cw[3 * g + 1] << 8 | cw[3 * g + 2];
        int v = ysf_golay(w);
        if (v < 0)
            return -1;
        word = word << 12 | (unsigned)v;
    }
    for (unsigned i = 0; i < 6u; i++)
        raw[i] = (uint8_t)(word >> (40u - 8u * i));
    if (ysf_crc(raw, 4) != (uint16_t)(raw[4] << 8 | raw[5]))
        return -1;
    for (unsigned i = 0; i < 4u; i++)
        out[i] = raw[i];
    return 0;
}

/* CSD field 1 or 2 into out[20]: 1 if its CRC is good */
static int ysf_csd(ysf_rx *r, int field, uint8_t out[20]) {
    uint8_t raw[23];
    ysf_viterbi5(r, field, 180, raw, 176);
    if (ysf_crc(raw, 20) != (uint16_t)(raw[20] << 8 | raw[21]))
        return 0;
    for (unsigned i = 0; i < 20u; i++)
        out[i] = (uint8_t)(raw[i] ^ r->white[i]);
    return 1;
}

/* coded bit ci of the sotto VW payload: spread (ci % 24) * 30 + ci / 24, PN9 off */
static float ysf_coded7(const ysf_rx *r, unsigned ci) {
    unsigned p = (ci % 24u) * 30u + ci / 24u;
    float v = ysf_bit(r, YSF_PAY_AT + p);
    return ((r->pn[p >> 3] >> (7u - (p & 7u))) & 1u) ? -v : v;
}

/* the sotto VW payload: tail-biting K=7 (133, 171, 165) at rate 7/18 (g3 on rows 0, 2, 4, 6 of 7),
   decoded with the wrap-around trick; five superframes into out[30], their CRC-8 flags back */
static uint8_t ysf_sotto(ysf_rx *r, uint8_t out[30]) {
    static const uint8_t OFF[7] = {0, 3, 5, 8, 10, 13, 15};
    float pm[64], nw[64];
    const unsigned n = YSF_NU + 2u * YSF_WRAP;
    for (unsigned s = 0; s < 64u; s++)
        pm[s] = 0.0f;
    for (unsigned t = 0; t < n; t++) {
        unsigned i = (t + YSF_NU - YSF_WRAP) % YSF_NU, ci = 18u * (i / 7u) + OFF[i % 7u];
        float l0 = ysf_coded7(r, ci), l1 = ysf_coded7(r, ci + 1u);
        float l2 = ((i % 7u) & 1u) ? 0.0f : ysf_coded7(r, ci + 2u);
        uint64_t sv = 0;
        float lo = 1e30f;
        for (unsigned ns = 0; ns < 64u; ns++) {
            float best = 0.0f;
            unsigned bx = 0;
            for (unsigned x = 0; x < 2u; x++) {
                unsigned s = (ns >> 1) | (x << 5), reg = ((s << 1) | (ns & 1u)) & 0x7Fu;
                float v = pm[s];
                v -= ysf_parity(reg & 0133u) ? -l0 : l0;
                v -= ysf_parity(reg & 0171u) ? -l1 : l1;
                v -= ysf_parity(reg & 0165u) ? -l2 : l2;
                if (x == 0 || v < best) {
                    best = v;
                    bx = x;
                }
            }
            nw[ns] = best;
            if (best < lo)
                lo = best;
            sv |= (uint64_t)bx << ns;
        }
        r->surv.k7[t] = sv;
        for (unsigned s = 0; s < 64u; s++)
            pm[s] = nw[s] - lo;
    }
    unsigned s = 0;
    for (unsigned i = 1; i < 64u; i++)
        if (pm[i] < pm[s])
            s = i;
    uint8_t u[35];
    for (unsigned i = 0; i < sizeof u; i++)
        u[i] = 0;
    for (unsigned t = n; t-- > 0;) {
        if (t >= YSF_WRAP && t < YSF_WRAP + YSF_NU && (s & 1u)) {
            unsigned k = t - YSF_WRAP;
            u[k >> 3] |= (uint8_t)(0x80u >> (k & 7u));
        }
        unsigned x = (unsigned)(r->surv.k7[t] >> s) & 1u;
        s = (s >> 1) | (x << 5);
    }
    uint8_t ok = 0;
    for (unsigned k = 0; k < 5u; k++) {
        for (unsigned i = 0; i < 6u; i++)
            out[6u * k + i] = u[7u * k + i];
        if (ysf_crc8(u + 7u * k) == u[7u * k + 6u])
            ok |= (uint8_t)(1u << k);
    }
    return ok;
}

void ysf_init(ysf_rx *r) {
    uint8_t *p = (uint8_t *)r;
    for (unsigned i = 0; i < sizeof *r; i++)
        p[i] = 0;
    unsigned s = 0x1FFu;
    for (unsigned i = 0; i < 720u; i++)
        if (ysf_pn9(&s))
            r->pn[i >> 3] |= (uint8_t)(0x80u >> (i & 7u));
    s = 0x1C9u;
    for (unsigned i = 0; i < 160u; i++)
        if (ysf_pn9(&s))
            r->white[i >> 3] |= (uint8_t)(0x80u >> (i & 7u));
}

static void ysf_fill(ysf_frame *f, const uint8_t b[4]) {
    for (unsigned i = 0; i < 4u; i++)
        f->fich[i] = b[i];
    f->fi = b[0] >> 6;
    f->fn = (b[1] >> 3) & 7u;
    f->ft = b[1] & 7u;
    f->dt = b[2] & 3u;
    f->sotto = b[2] >> 7;
    f->dgid = b[3] & 0x7Fu;
}

/* the frame whose 480 symbols are in: 1 if it counts */
static int ysf_frame_read(ysf_rx *r, ysf_frame *f) {
    float c = 0.0f, pp = 0.0f;
    for (unsigned k = 0; k < YSF_NSYNC; k++) {
        float p = ysf_sync_level(k);
        c += (float)r->sym[k] * p;
        pp += p * p;
    }
    float gain = c / pp;
    f->at = r->cand;
    f->score = r->cscore;
    f->gain = gain / YSF_YSCALE;
    f->metric = 1.0f;
    f->pay = YSF_PAY_NONE;
    f->ok = 0;
    if (gain <= 0.0f)
        return 0;
    r->inv = 1.0f / gain;
    uint8_t b[4];
    if (ysf_fich(r, b, &f->metric) == 0) {
        f->fich_ok = 1;
        ysf_fill(f, b);
        for (unsigned i = 0; i < 4u; i++)
            r->last[i] = b[i];
        r->last_fn = f->fn;
    } else if (r->locked && (r->last[0] >> 6) == YSF_FI_COMMS && (r->last[2] & 0x83u) == 0x83u) {
        /* locked on sotto VW: read it on as the next frame, the CRC-8s decide */
        f->fich_ok = 0;
        ysf_fill(f, r->last);
        f->fn = (uint8_t)((r->last_fn + 1u) % (f->ft + 1u));
        r->last_fn = f->fn;
        f->fich[1] = (uint8_t)((f->fich[1] & 0xC7u) | f->fn << 3);
        r->bad++;
    } else {
        return 0;
    }
    if (f->fi == YSF_FI_HEADER || f->fi == YSF_FI_TERM || (f->fi == YSF_FI_COMMS && f->dt == YSF_DT_DATA_FR)) {
        f->pay = f->dt == YSF_DT_DATA_FR && f->fi == YSF_FI_COMMS ? YSF_PAY_DATA_FR : YSF_PAY_CSD;
        f->ok = (uint8_t)(ysf_csd(r, 1, f->data) | ysf_csd(r, 2, f->data + 20) << 1);
    } else if (f->fi == YSF_FI_COMMS && f->dt == YSF_DT_VOICE_FR && f->sotto) {
        f->pay = YSF_PAY_SOTTO;
        f->ok = ysf_sotto(r, f->data);
    }
    return f->fich_ok || f->ok;
}

int ysf_push(ysf_rx *r, int16_t s, ysf_frame *out) {
    float xs = (float)s;
    if (YSF_INVERT)
        xs = -xs;
    r->dc += (xs - r->dc) * (1.0f / 512.0f);
    float xd = xs - r->dc;
    if (xd > 32767.0f)
        xd = 32767.0f;
    if (xd < -32768.0f)
        xd = -32768.0f;
    r->x[r->xi & 31u] = (int16_t)xd;
    r->xi++;
    int got = 0;
    for (unsigned j = 0; j < 3u; j++) {
        float acc = 0.0f;
        for (unsigned m = 0; 3u * m + j < YSF_NTAP; m++)
            acc += YSF_RRC[3u * m + j] * (float)r->x[(r->xi - 1u - m) & 31u];
        uint32_t n = r->yn++;
        acc *= YSF_YSCALE;
        r->y[n & (YSF_YN - 1u)] = (int16_t)(acc > 32767.0f ? 32767.0f : acc < -32768.0f ? -32768.0f : acc);
        if (n < 128u)
            continue;
        uint32_t start = n - YSF_SPS * (YSF_NSYNC - 1u);   /* the sync's symbols end at n */
        int begin = 0;
        if (r->state == 0 || r->state == 1) {
            float sc = ysf_score(r, start);
            if (sc >= YSF_SYNC_MIN * YSF_SYNC_MIN && (r->state == 0 || sc > r->cscore)) {
                r->state = 1;
                r->cand = start;
                r->cscore = sc;
            } else if (r->state == 1 && start - r->cand > 2u) {
                begin = 1;              /* the peak passed */
            }
        } else if (r->state == 3 && start == r->next + YSF_TRACK) {
            float bs = -2.0f;
            uint32_t bst = r->next;
            for (int d = -YSF_TRACK; d <= YSF_TRACK; d++) {
                float sc = ysf_score(r, r->next + (uint32_t)d);
                if (sc > bs) {
                    bs = sc;
                    bst = r->next + (uint32_t)d;
                }
            }
            /* a weak sync still reads at the expected point: the FICH or the CRCs decide */
            r->cand = bs >= YSF_SYNC_KEEP * YSF_SYNC_KEEP ? bst : r->next;
            r->cscore = bs;
            begin = 1;
        }
        if (begin) {
            r->state = 2;
            r->nsym = 0;
            while (r->nsym < YSF_NSYM && r->cand + YSF_SPS * r->nsym <= n) {
                r->sym[r->nsym] = r->y[(r->cand + YSF_SPS * r->nsym) & (YSF_YN - 1u)];
                r->nsym++;
            }
        } else if (r->state == 2 && n == r->cand + YSF_SPS * r->nsym) {
            r->sym[r->nsym++] = r->y[n & (YSF_YN - 1u)];
        }
        if (r->state != 2 || r->nsym < YSF_NSYM)
            continue;
        int was_locked = r->locked;
        if (!ysf_frame_read(r, out)) {
            if (was_locked && ++r->misses < YSF_MISSES) {
                r->next = r->cand + YSF_FRAME;
                r->state = 3;
            } else {
                if (was_locked)
                    r->lost++;
                r->locked = 0;
                r->state = 0;
            }
            continue;
        }
        r->frames++;
        r->misses = out->fich_ok ? 0 : r->misses;
        got = 1;
        r->next = r->cand + YSF_FRAME;
        r->locked = out->fi != YSF_FI_TERM;
        r->state = r->locked ? 3 : 0;
    }
    return got;
}
