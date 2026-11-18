/* M17 receiver for 8 kHz discriminator audio (m17_rx.h). SPDX-License-Identifier: MIT */
#include "m17_rx.h"

/* RRC alpha 0.5, 5 samples a symbol, +-4 symbols, summing to 3 (the x3 interpolator's gain):
   modem.m17.rrc(0.5, 5, 4) * 3 */
static const float M17_RRC[M17_NTAP] = {
    -0.00606461f, -0.00368288f, 0.00293582f, 0.00903683f, 0.00915610f, 0.00181938f, -0.00804393f,
    -0.01167157f, -0.00323410f, 0.01369327f, 0.02547134f, 0.01603504f, -0.02037293f, -0.06926368f,
    -0.09674485f, -0.06367835f, 0.05256077f, 0.24042256f, 0.45181338f, 0.61873878f, 0.68214728f,
    0.61873878f, 0.45181338f, 0.24042256f, 0.05256077f, -0.06367835f, -0.09674485f, -0.06926368f,
    -0.02037293f, 0.01603504f, 0.02547134f, 0.01369327f, -0.00323410f, -0.01167157f, -0.00804393f,
    0.00181938f, 0.00915610f, 0.00903683f, 0.00293582f, -0.00368288f, -0.00606461f};

/* LSF, stream, packet, BERT, EOT */
static const uint16_t M17_SYNCW[M17_KINDS] = {0x55F7, 0xFF5D, 0x75FF, 0xDF55, 0x555D};

static const uint8_t M17_DECORR[46] = {
    0xd6, 0xb5, 0xe2, 0x30, 0x82, 0xff, 0x84, 0x62, 0xba, 0x4e, 0x96, 0x90, 0xd8, 0x98, 0xdd, 0x5d,
    0x0c, 0xc8, 0x52, 0x43, 0x91, 0x1d, 0xf8, 0x6e, 0x68, 0x2f, 0x35, 0xda, 0x14, 0xea, 0xcd, 0x76,
    0x19, 0x8d, 0xd5, 0x80, 0xd1, 0x33, 0x87, 0x13, 0x57, 0x18, 0x2d, 0x29, 0x78, 0xc3};

static const uint16_t M17_GOLAY[12] = {0x8eb, 0x93e, 0xa97, 0xdc6, 0x367, 0x6cd,
                                       0xd99, 0x3da, 0x7b4, 0xf68, 0x63b, 0xc75};

static int m17_level(unsigned dibit) {
    int v = (dibit & 1u) ? 3 : 1;
    return (dibit & 2u) ? -v : v;
}

static void m17_sync_levels(int kind, float *p) {
    unsigned w = M17_SYNCW[kind];
    for (unsigned k = 0; k < 8u; k++)
        p[k] = (float)m17_level((w >> (14u - 2u * k)) & 3u);
}

static float m17_yat(const m17_rx *r, uint32_t i) {
    return (float)r->y[i & (M17_YN - 1u)] * (1.0f / M17_YSCALE);
}

/* the sync correlation of the 8 symbols from 24 kHz sample `start`: c / sqrt(e * pp) (1 = exact) as
   its square with the sign kept (no sqrt in a module), and the fitted gain */
static float m17_score(const m17_rx *r, uint32_t start, int kind, float *gain) {
    float p[8], c = 0.0f, e = 0.0f, pp = 0.0f;
    m17_sync_levels(kind, p);
    for (unsigned k = 0; k < 8u; k++) {
        float v = m17_yat(r, start + M17_SPS * k);
        c += v * p[k];
        e += v * v;
        pp += p[k] * p[k];
    }
    if (gain)
        *gain = c / pp;
    if (e <= 0.0f)
        return 0.0f;
    float s = c * c / (e * pp);
    return c < 0.0f ? -s : s;
}

static int m17_best(const m17_rx *r, uint32_t start, float *score) {
    int best = 0;
    float bs = -2.0f;
    for (int k = 0; k < M17_KINDS; k++) {
        float s = m17_score(r, start, k, 0);
        if (s > bs) {
            bs = s;
            best = k;
        }
    }
    *score = bs;
    return best;
}

static unsigned m17_parity(unsigned v) {
    v ^= v >> 4;
    v ^= v >> 2;
    v ^= v >> 1;
    return v & 1u;
}

/* soft K=5 Viterbi over n pairs (+ = bit 0), bits (flush included) MSB first into out; returns the
   share of the soft values' weight the best path disagrees with (0 clean, about 0.3 or more noise) */
static float m17_viterbi(m17_rx *r, const float *soft, unsigned n, uint8_t *out, unsigned nbits) {
    float *pm = r->pm, nw[16], sum = 0.0f;
    for (unsigned i = 0; i < 2u * n; i++)
        sum += soft[i] < 0.0f ? -soft[i] : soft[i];
    for (unsigned s = 0; s < 16u; s++)
        pm[s] = s ? 1e30f : 0.0f;
    for (unsigned k = 0; k < n; k++) {
        float a = soft[2 * k], c = soft[2 * k + 1];
        uint16_t sv = 0;
        for (unsigned ns = 0; ns < 16u; ns++) {
            float best = 0.0f;
            unsigned bx = 0;
            for (unsigned x = 0; x < 2u; x++) {
                unsigned s = (ns >> 1) | (x << 3), m = ((s << 1) | (ns & 1u)) & 0x1Fu;
                float e1 = m17_parity(m & 0x19u) ? -1.0f : 1.0f, e2 = m17_parity(m & 0x17u) ? -1.0f : 1.0f;
                float v = pm[s] - a * e1 - c * e2;
                if (x == 0 || v < best) {
                    best = v;
                    bx = x;
                }
            }
            nw[ns] = best;
            sv |= (uint16_t)(bx << ns);
        }
        r->surv[k] = sv;
        for (unsigned s = 0; s < 16u; s++)
            pm[s] = nw[s];
    }
    unsigned s = 0;
    for (unsigned i = 1; i < 16u; i++)
        if (pm[i] < pm[s])
            s = i;
    float metric = sum > 0.0f ? (sum + pm[s]) / (2.0f * sum) : 1.0f;    /* 0 = every bit agrees */
    for (unsigned i = 0; i < (nbits + 7u) / 8u; i++)
        out[i] = 0;
    for (unsigned k = n; k-- > 0;) {
        if (k < nbits && (s & 1u))
            out[k >> 3] |= (uint8_t)(0x80u >> (k & 7u));
        unsigned x = (r->surv[k] >> s) & 1u;
        s = (s >> 1) | (x << 3);
    }
    return metric;
}

/* kept positions of the puncture pattern (P1 LSF, P2 stream / BERT, P3 packet) */
static int m17_kept(int pat, unsigned i) {
    if (pat == 1) {
        unsigned j = i % 61u;
        return j == 60u || (j & 3u) != 2u;
    }
    if (pat == 2)
        return i % 12u != 11u;
    return (i & 7u) != 7u;
}

static void m17_depuncture(const float *in, unsigned nin, float *out, unsigned n_out, int pat) {
    unsigned k = 0;
    for (unsigned i = 0; i < n_out; i++)
        out[i] = (m17_kept(pat, i) && k < nin) ? in[k++] : 0.0f;
}

static unsigned m17_golay_enc(unsigned d) {
    unsigned c = 0;
    for (unsigned i = 0; i < 12u; i++)
        if ((d >> i) & 1u)
            c ^= M17_GOLAY[i];
    return c;
}

static unsigned m17_weight(unsigned v) {
    unsigned n = 0;
    for (; v; v &= v - 1u)
        n++;
    return n;
}

/* 24-bit codeword -> 12 data bits, up to 3 errors; -1 if more */
static int m17_golay(uint32_t cw) {
    unsigned d = (cw >> 12) & 0xFFFu, c = cw & 0xFFFu;
    for (unsigned w = 0; w <= 3u; w++) {
        /* every data error pattern of weight w, with the parity errors it leaves */
        for (unsigned e = 0; e < 4096u; e++) {
            if (m17_weight(e) != w)
                continue;
            if (m17_weight(c ^ m17_golay_enc(d ^ e)) + w <= 3u)
                return (int)(d ^ e);
        }
    }
    return -1;
}

void m17_init(m17_rx *r) {
    uint8_t *p = (uint8_t *)r;
    for (unsigned i = 0; i < sizeof *r; i++)
        p[i] = 0;
    for (unsigned i = 0; i < M17_NTAP; i++)
        r->h[i] = M17_RRC[i];
}

uint16_t m17_crc(const uint8_t *d, unsigned n) {
    unsigned crc = 0xFFFFu;
    for (unsigned i = 0; i < n; i++) {
        crc ^= (unsigned)d[i] << 8;
        for (unsigned b = 0; b < 8u; b++)
            crc = (crc & 0x8000u) ? ((crc << 1) ^ 0x5935u) : (crc << 1);
        crc &= 0xFFFFu;
    }
    return (uint16_t)crc;
}

void m17_callsign(const uint8_t *b, char *out) {
    static const char cs[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/.";
    uint8_t v[6];
    unsigned all = 1, n = 0;
    for (unsigned i = 0; i < 6u; i++) {
        v[i] = b[i];
        all &= b[i] == 0xFFu;
    }
    if (all) {
        out[0] = 'A', out[1] = 'L', out[2] = 'L', out[3] = 0;
        return;
    }
    for (;;) {                          /* long division by 40, least significant digit first */
        unsigned rem = 0, nz = 0;
        for (unsigned i = 0; i < 6u; i++) {
            unsigned cur = (rem << 8) | v[i];
            v[i] = (uint8_t)(cur / 40u);
            rem = cur % 40u;
            nz |= v[i];
        }
        if (n < 9u)
            out[n++] = cs[rem];
        if (!nz)
            break;
    }
    out[n] = 0;
}

/* the frame whose sync starts at r->cand: levels, soft bits, decode */
static void m17_frame_read(m17_rx *r, m17_frame *f) {
    float gain;
    f->kind = r->ckind;
    f->at = r->cand;
    f->score = r->cscore;
    m17_score(r, r->cand, r->ckind, &gain);
    f->gain = gain;
    f->ok = 0;
    f->metric = 0.0f;
    if (r->ckind == M17_EOT || gain == 0.0f) {
        f->ok = r->ckind == M17_EOT;
        return;
    }
    float *raw = r->raw, inv = 1.0f / gain;
    for (unsigned k = 8; k < 192u; k++) {
        float v = m17_yat(r, r->cand + M17_SPS * k) * inv;
        if (v > 4.0f)
            v = 4.0f;
        if (v < -4.0f)
            v = -4.0f;
        unsigned j = 2u * (k - 8u);
        float a = v, b = 2.0f - (v < 0.0f ? -v : v);
        if ((M17_DECORR[j >> 3] >> (7u - (j & 7u))) & 1u)
            a = -a;
        if ((M17_DECORR[(j + 1u) >> 3] >> (7u - ((j + 1u) & 7u))) & 1u)
            b = -b;
        raw[j] = a;
        raw[j + 1u] = b;
    }
    /* de-interleave in place of nothing: d[i] = raw[PERM[i]] into soft[0..367] */
    float *d = r->soft;
    for (uint32_t i = 0; i < 368u; i++)
        d[i] = raw[(45u * i + 92u * i * i) % 368u];
    for (uint32_t i = 0; i < 368u; i++)
        raw[i] = d[i];
    switch (r->ckind) {
    case M17_LSF:
        m17_depuncture(raw, 368, d, 488, 1);
        f->metric = m17_viterbi(r, d, 244, f->data, 240);
        f->ok = m17_crc(f->data, 28) == (uint16_t)((f->data[28] << 8) | f->data[29]);
        break;
    case M17_STREAM: {
        int ok = 1;
        for (unsigned g = 0; g < 4u; g++) {
            uint32_t cw = 0;
            for (unsigned i = 0; i < 24u; i++)
                cw = (cw << 1) | (raw[24u * g + i] < 0.0f);
            int v = m17_golay(cw);
            if (v < 0) {
                ok = 0;
                v = (int)(cw >> 12);
            }
            /* 12 bits a block into lich[] */
            unsigned bit = 12u * g;
            for (unsigned i = 0; i < 12u; i++, bit++) {
                if (bit < 48u) {
                    if ((v >> (11u - i)) & 1)
                        f->lich[bit >> 3] |= (uint8_t)(0x80u >> (bit & 7u));
                    else
                        f->lich[bit >> 3] &= (uint8_t)~(0x80u >> (bit & 7u));
                }
            }
        }
        m17_depuncture(raw + 96, 272, d, 296, 2);
        f->metric = m17_viterbi(r, d, 148, f->data, 144);
        f->ok = ok;
        break;
    }
    case M17_PACKET:
        m17_depuncture(raw, 368, d, 424, 3);
        f->metric = m17_viterbi(r, d, 212, f->data, 208);
        f->ok = 1;
        break;
    case M17_BERT:
        m17_depuncture(raw, 368, d, 402, 2);
        f->metric = m17_viterbi(r, d, 201, f->data, 197);
        f->ok = 1;
        break;
    }
}

int m17_push(m17_rx *r, int16_t s, m17_frame *out) {
    float xs = (float)s;
    if (M17_INVERT)
        xs = -xs;
    r->dc += (xs - r->dc) * (1.0f / 512.0f);
    float xd = xs - r->dc;
    if (xd > 32767.0f)
        xd = 32767.0f;
    if (xd < -32768.0f)
        xd = -32768.0f;
    r->x[r->xi & 15u] = (int16_t)xd;
    r->xi++;
    int got = 0;
    for (unsigned j = 0; j < 3u; j++) {
        float acc = 0.0f;
        for (unsigned m = 0; 3u * m + j < M17_NTAP; m++)
            acc += r->h[3u * m + j] * (float)r->x[(r->xi - 1u - m) & 15u];
        uint32_t n = r->yn++;
        acc *= M17_YSCALE;              /* kept as 16 bits: 2 KB of RAM saved for a module */
        r->y[n & (M17_YN - 1u)] = (int16_t)(acc > 32767.0f ? 32767.0f : acc < -32768.0f ? -32768.0f : acc);
        if (n < 40u)
            continue;
        uint32_t start = n - 35u;          /* the 8 sync symbols end at n */
        float sc;
        if (r->state == 0 || r->state == 1) {
            int k = m17_best(r, start, &sc);
            if (sc >= M17_SYNC_MIN * M17_SYNC_MIN && (r->state == 0 || sc > r->cscore)) {
                r->state = 1;
                r->cand = start;
                r->cscore = sc;
                r->ckind = k;
            } else if (r->state == 1 && start - r->cand > 2u) {
                r->state = 2;               /* the peak passed: read the frame when it is in */
            }
        } else if (r->state == 3 && start == r->next + M17_TRACK) {
            float bs = -2.0f;
            int bk = 0;
            uint32_t bst = r->next;
            for (int d = -M17_TRACK; d <= M17_TRACK; d++) {
                int k = m17_best(r, r->next + (uint32_t)d, &sc);
                if (sc > bs) {
                    bs = sc;
                    bk = k;
                    bst = r->next + (uint32_t)d;
                }
            }
            if (bs >= M17_SYNC_KEEP * M17_SYNC_KEEP) {
                r->cand = bst;
                r->cscore = bs;
                r->ckind = bk;
                r->state = 2;
                r->misses = 0;
            } else if (++r->misses >= M17_MISSES) {
                r->state = 0;
                r->locked = 0;
                r->lost++;
            } else {
                r->next += M17_FRAME;
            }
        }
        if (r->state == 2 && n >= r->cand + 191u * M17_SPS) {
            m17_frame_read(r, out);
            out->score = r->cscore;
            int was_locked = r->locked;
            /* an EOT counts only after frames (a preamble's +3 -3 is in its pattern) */
            if ((out->metric > (was_locked ? M17_Q_KEEP : M17_Q_MIN) && !(out->kind == M17_LSF && out->ok))
                || (out->kind == M17_EOT && !was_locked)) {
                /* noise that matched a sync: hunt on (or count a miss when locked) */
                if (was_locked && ++r->misses < M17_MISSES) {
                    r->next = r->cand + M17_FRAME;
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
            got = 1;
            r->next = r->cand + M17_FRAME;
            r->locked = r->ckind != M17_EOT;
            r->state = r->locked ? 3 : 0;
        }
    }
    return got;
}
