/* Phase-continuous FFSK for the K6 pager's F profile (modem/fsk.py ffsk): bit 1 = mark
 * (1200 Hz), bit 0 = space (1800 Hz at 1800 baud and below, 2400 Hz at 2400), any sample rate,
 * integer only. Bits come from a pt_air (pager_tx.h). Output is signed 16-bit samples with a
 * 5 ms raised-cosine ramp at each end, for a DAC, a PWM duty cycle or a file.
 *
 * The bit clock is fractional (acc += baud each sample, a new bit when it passes fs), so 1800 baud
 * works at 48 kHz (26.67 samples per bit) as well as at an integer ratio.
 *
 * SPDX-License-Identifier: MIT */
#ifndef FFSK_TX_H
#define FFSK_TX_H

#include <stdint.h>

#include "pager_tx.h"

typedef struct {
    pt_air *air;
    int (*next)(void *ctx);     /* the bit source: the next bit, or -1 at the end */
    void *ctx;
    uint32_t fs, baud, inc_mark, inc_space, phase, acc;
    int16_t level;              /* peak, out of 32767 */
    unsigned ramp, n, tail;     /* ramp length, samples so far, ramp-down samples left */
    int bit, done;
} ffsk_tx;

/* mark_hz / space_hz 0: the K6's pair for the baud rate (1200/1800, or 1200/2400 at 2400). */
void ffsk_init(ffsk_tx *m, pt_air *air, uint32_t fs, uint32_t baud, uint32_t mark_hz,
               uint32_t space_hz, int16_t level);

/* The same with any bit source (c/common/range_tx.h's rg_air_next for PHY R). */
void ffsk_init_src(ffsk_tx *m, int (*next)(void *ctx), void *ctx, uint32_t fs, uint32_t baud,
                   uint32_t mark_hz, uint32_t space_hz, int16_t level);

/* Fills up to n samples; returns how many (fewer than n only at the end of the burst, then 0). */
unsigned ffsk_render(ffsk_tx *m, int16_t *out, unsigned n);

#endif
