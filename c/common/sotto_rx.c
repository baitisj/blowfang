/* sotto_rx: see sotto_rx.h. SPDX-License-Identifier: MIT */
#include "sotto_rx.h"

void srx_init(srx_state *s, uint8_t *frames, uint8_t *have, unsigned max) {
    uint8_t *z = (uint8_t *)s;
    for (unsigned i = 0; i < sizeof *s; i++)
        z[i] = 0;
    s->frames = frames;
    s->have = have;
    s->max = max;
    for (unsigned i = 0; i < max / 8u + 1u; i++)
        have[i] = 0;
}

static int srx_has(const srx_state *s, unsigned k) {
    return (s->have[k >> 3] >> (k & 7u)) & 1u;
}

int srx_put(srx_state *s, int32_t idx, const uint8_t *sf) {
    if (!s->based) {
        s->based = 1;
        s->base = idx;
    }
    int32_t k = idx - s->base;
    if (k < 0 || (uint32_t)k >= s->max) {
        s->over++;
        return 0;
    }
    if (srx_has(s, (unsigned)k))
        return 0;
    for (unsigned i = 0; i < SRX_SF; i++)
        s->frames[(unsigned)k * SRX_SF + i] = sf[i];
    s->have[(unsigned)k >> 3] |= (uint8_t)(1u << ((unsigned)k & 7u));
    s->got++;
    if ((unsigned)k + 1u > s->n)
        s->n = (unsigned)k + 1u;
    return 1;
}

uint16_t srx_crc16(const uint8_t *d, unsigned n) {
    uint16_t c = 0xFFFFu;
    for (unsigned i = 0; i < n; i++) {
        c ^= (uint16_t)(d[i] << 8);
        for (unsigned b = 0; b < 8u; b++)
            c = (uint16_t)((c & 0x8000u) ? ((unsigned)c << 1) ^ 0x5935u : (unsigned)c << 1);
    }
    return c;
}

int srx_m17(srx_state *s, const m17_frame *f) {
    if (f->kind == M17_EOT) {           /* ends a message, not the quiet before the next one */
        s->ended = s->got != 0;
        return 0;
    }
    if (f->kind != M17_STREAM)
        return 0;
    unsigned fn = ((unsigned)f->data[0] << 8) | f->data[1];
    const uint8_t *p = f->data + 2;
    unsigned last = fn >> 15;
    fn &= 0x7FFFu;
    if (srx_crc16(p, 12) != (uint16_t)((p[12] << 8) | p[13]) || (p[14] >> 4) != 1u) {
        s->bad++;
        s->ended = last && s->got;
        return 0;
    }
    int got = 0;
    if (p[14] & 1u) {                   /* real time: a is new, b repeats the one before */
        got += srx_put(s, (int32_t)fn, p);
        if (fn)
            got += srx_put(s, (int32_t)fn - 1, p + SRX_SF);
    } else {
        got += srx_put(s, 2 * (int32_t)fn, p);
        got += srx_put(s, 2 * (int32_t)fn + 1, p + SRX_SF);
    }
    if (last && s->got)
        s->ended = 1;
    return got;
}

int srx_ysf(srx_state *s, const ysf_frame *f) {
    if (f->fi == YSF_FI_TERM && f->fich_ok) {
        s->ended = s->got != 0;
        return 0;
    }
    if (f->pay != YSF_PAY_SOTTO)
        return 0;
    if (!s->based)
        s->at0 = f->at;
    uint32_t d = f->at - s->at0;        /* wraps: frames after the first only */
    int32_t frame = (int32_t)((d + SRX_YSF_FRAME / 2u) / SRX_YSF_FRAME);
    int got = 0;
    for (unsigned k = 0; k < 5u; k++) {
        if (!((f->ok >> k) & 1u)) {
            s->bad++;
            continue;
        }
        got += srx_put(s, 5 * frame + (int32_t)k, f->data + SRX_SF * k);
    }
    return got;
}

unsigned srx_conceal(srx_state *s) {
    unsigned missing = 0, last = s->n;
    for (unsigned k = 0; k < s->n; k++) {
        if (srx_has(s, k)) {
            last = k;
            continue;
        }
        missing++;
        unsigned src = last;
        if (src >= s->n)
            for (src = k + 1u; src < s->n && !srx_has(s, src); src++) {
            }
        for (unsigned i = 0; i < SRX_SF; i++)
            s->frames[k * SRX_SF + i] = src < s->n ? s->frames[src * SRX_SF + i] : 0;
    }
    return missing;
}
