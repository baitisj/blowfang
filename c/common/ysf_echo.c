/* ysf_echo.h: a YSF call kept frame by frame for a parrot. SPDX-License-Identifier: MIT */
#include "ysf_echo.h"

void ye_init(ye_state *e, uint8_t *frames, unsigned max) {
    uint8_t *p = (uint8_t *)e;
    for (unsigned i = 0; i < sizeof *e; i++)
        p[i] = 0;
    e->frames = frames;
    e->max = max;
    e->src[0] = '?';
}

/* symbol level (units of one level step) -> dibit: +1 00, +3 01, -1 10, -3 11 (ysf_bit's) */
void ye_bytes(const ysf_rx *r, uint8_t out[YE_FRAME]) {
    for (unsigned i = 0; i < YE_FRAME; i++) {
        unsigned b = 0;
        for (unsigned k = 0; k < 4u; k++) {
            float v = (float)r->sym[4u * i + k] * r->inv;
            unsigned d = (v < 0.0f ? 2u : 0u) | ((v < -2.0f || v > 2.0f) ? 1u : 0u);
            b = b << 2 | d;
        }
        out[i] = (uint8_t)b;
    }
}

int ye_put(ye_state *e, const ysf_rx *r, const ysf_frame *f) {
    if (e->n) {
        uint32_t d = f->at - e->last_at;
        unsigned k = (unsigned)((d + YSF_FRAME / 2u) / YSF_FRAME);
        if (k > 1u)
            e->gaps += k - 1u;
    }
    e->last_at = f->at;
    if (f->fi == YSF_FI_HEADER && f->pay == YSF_PAY_CSD && (f->ok & 1u)) {
        unsigned n = 10;
        while (n && f->data[10u + n - 1u] == ' ')
            n--;
        for (unsigned i = 0; i < n; i++)
            e->src[i] = (f->data[10u + i] >= 32u && f->data[10u + i] < 127u) ? (char)f->data[10u + i] : '?';
        e->src[n ? n : 1u] = 0;
    }
    int term = f->fi == YSF_FI_TERM && f->fich_ok;
    if (term)
        e->ended = 1;
    /* the last place is the terminator's: a call longer than the room still ends properly */
    if (e->n >= e->max || (e->n + 1u >= e->max && !term && e->max > 1u)) {
        e->dropped++;
        return 0;
    }
    ye_bytes(r, e->frames + e->n * YE_FRAME);
    e->n++;
    if (f->fi == YSF_FI_HEADER)
        e->hdr++;
    else if (f->fi == YSF_FI_TERM)
        e->term++;
    else if (f->fi == YSF_FI_COMMS && (f->dt == YSF_DT_VD2 || f->dt == YSF_DT_VD1 || f->dt == YSF_DT_VOICE_FR))
        e->voice++;
    else
        e->other++;
    return 1;
}
