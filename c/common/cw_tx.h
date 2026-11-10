/* Morse ident as audio, for the station identification every UV-380 test transmission carries
 * (AG7EW): the same keying as modem.k6.cw (3 dits of silence first, 1-dit gaps inside a letter,
 * 3 between letters, 7 between words, 800 Hz, 20 wpm, 5 ms edges). Integer only, streams into a
 * caller's buffer like ffsk_tx.
 * SPDX-License-Identifier: MIT */
#ifndef CW_TX_H
#define CW_TX_H

#include <stdint.h>

typedef struct {
    const char *text;
    const char *el;             /* the current letter's remaining dots and dashes */
    uint32_t phase, inc;
    unsigned dit, left, ramp, gain;
    int16_t level;
    int key, lead, done;
} cw_tx;

void cw_init(cw_tx *c, const char *text, uint32_t fs, unsigned wpm, uint32_t tone_hz, int16_t level);

/* Fills up to n samples; returns how many (0 at the end). */
unsigned cw_render(cw_tx *c, int16_t *out, unsigned n);

#endif
