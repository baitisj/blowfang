/* FFSK modulator (ffsk_tx.h).
 * SPDX-License-Identifier: MIT */
#include "ffsk_tx.h"

/* sin(2 pi i / 256) in Q15 */
static const int16_t SINE[256] = {
    0, 804, 1608, 2410, 3212, 4011, 4808, 5602, 6393, 7179, 7962, 8739,
    9512, 10278, 11039, 11793, 12539, 13279, 14010, 14732, 15446, 16151, 16846, 17530,
    18204, 18868, 19519, 20159, 20787, 21403, 22005, 22594, 23170, 23731, 24279, 24811,
    25329, 25832, 26319, 26790, 27245, 27683, 28105, 28510, 28898, 29268, 29621, 29956,
    30273, 30571, 30852, 31113, 31356, 31580, 31785, 31971, 32137, 32285, 32412, 32521,
    32609, 32678, 32728, 32757, 32767, 32757, 32728, 32678, 32609, 32521, 32412, 32285,
    32137, 31971, 31785, 31580, 31356, 31113, 30852, 30571, 30273, 29956, 29621, 29268,
    28898, 28510, 28105, 27683, 27245, 26790, 26319, 25832, 25329, 24811, 24279, 23731,
    23170, 22594, 22005, 21403, 20787, 20159, 19519, 18868, 18204, 17530, 16846, 16151,
    15446, 14732, 14010, 13279, 12539, 11793, 11039, 10278, 9512, 8739, 7962, 7179,
    6393, 5602, 4808, 4011, 3212, 2410, 1608, 804, 0, -804, -1608, -2410,
    -3212, -4011, -4808, -5602, -6393, -7179, -7962, -8739, -9512, -10278, -11039, -11793,
    -12539, -13279, -14010, -14732, -15446, -16151, -16846, -17530, -18204, -18868, -19519, -20159,
    -20787, -21403, -22005, -22594, -23170, -23731, -24279, -24811, -25329, -25832, -26319, -26790,
    -27245, -27683, -28105, -28510, -28898, -29268, -29621, -29956, -30273, -30571, -30852, -31113,
    -31356, -31580, -31785, -31971, -32137, -32285, -32412, -32521, -32609, -32678, -32728, -32757,
    -32767, -32757, -32728, -32678, -32609, -32521, -32412, -32285, -32137, -31971, -31785, -31580,
    -31356, -31113, -30852, -30571, -30273, -29956, -29621, -29268, -28898, -28510, -28105, -27683,
    -27245, -26790, -26319, -25832, -25329, -24811, -24279, -23731, -23170, -22594, -22005, -21403,
    -20787, -20159, -19519, -18868, -18204, -17530, -16846, -16151, -15446, -14732, -14010, -13279,
    -12539, -11793, -11039, -10278, -9512, -8739, -7962, -7179, -6393, -5602, -4808, -4011,
    -3212, -2410, -1608, -804,
};

/* 0.5 - 0.5 cos(pi i / RAMP_STEPS) in Q15, the ramp shape, sampled at 33 points */
static const uint16_t RAMP[33] = {
    0, 79, 315, 705, 1247, 1935, 2761, 3719, 4799, 5990, 7282,
    8661, 10114, 11628, 13188, 14778, 16384, 17990, 19580, 21140, 22654, 24107,
    25486, 26778, 27969, 29049, 30007, 30833, 31521, 32063, 32453, 32689, 32768,
};

static uint32_t inc_for(uint32_t hz, uint32_t fs) {
    return (uint32_t)(((uint64_t)hz << 32) / fs);
}

static int air_next(void *ctx) { return pt_air_bit((pt_air *)ctx); }

void ffsk_init(ffsk_tx *m, pt_air *air, uint32_t fs, uint32_t baud, uint32_t mark_hz,
               uint32_t space_hz, int16_t level) {
    pt_air_start(air);
    ffsk_init_src(m, air_next, air, fs, baud, mark_hz, space_hz, level);
    m->air = air;
}

void ffsk_init_src(ffsk_tx *m, int (*next)(void *ctx), void *ctx, uint32_t fs, uint32_t baud,
                   uint32_t mark_hz, uint32_t space_hz, int16_t level) {
    if (!mark_hz)
        mark_hz = 1200;
    if (!space_hz)
        space_hz = baud >= 2400 ? 2400 : 1800;
    m->air = 0;
    m->next = next;
    m->ctx = ctx;
    m->fs = fs;
    m->baud = baud;
    m->inc_mark = inc_for(mark_hz, fs);
    m->inc_space = inc_for(space_hz, fs);
    m->phase = 0;
    m->acc = 0;
    m->level = level;
    m->ramp = fs / 200;                         /* 5 ms */
    m->n = 0;
    m->tail = 0;
    m->done = 0;
    m->bit = next(ctx);
    if (m->bit < 0) {
        m->done = 1;
    }
}

/* ramp gain in Q15 for sample k of r */
static int32_t ramp_gain(unsigned k, unsigned r) {
    if (k >= r)
        return 32768;
    uint32_t x = (uint32_t)(((uint64_t)k << 5 << 16) / r);      /* 0 .. 32 in Q16 */
    uint32_t i = x >> 16, f = x & 0xFFFF;
    return RAMP[i] + (int32_t)(((int64_t)(RAMP[i + 1] - RAMP[i]) * f) >> 16);
}

unsigned ffsk_render(ffsk_tx *m, int16_t *out, unsigned n) {
    unsigned i = 0;
    while (i < n) {
        if (m->done && m->tail == 0)
            break;
        int32_t g;
        if (m->done) {                          /* ramp down on the last tone */
            g = ramp_gain(--m->tail, m->ramp);
        } else {
            g = ramp_gain(m->n, m->ramp);
        }
        int32_t s = SINE[m->phase >> 24];
        out[i++] = (int16_t)((((s * m->level) >> 15) * g) >> 15);
        m->phase += m->bit ? m->inc_mark : m->inc_space;
        m->n++;
        if (!m->done) {
            m->acc += m->baud;
            if (m->acc >= m->fs) {
                m->acc -= m->fs;
                int b = m->next(m->ctx);
                if (b < 0) {
                    m->done = 1;
                    m->tail = m->ramp;
                } else {
                    m->bit = b;
                }
            }
        }
    }
    return i;
}
