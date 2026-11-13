/* PHY R receiver for a radio that hears the audio itself (docs/k6-range.md; the sender is
 * range_tx.h, the K6's own receiver is c/k6/mod/rangerx.c on its chip's sign samples). The UV-380
 * first: its MCU reads the FM receiver's audio off the HR_C6000's I2S port at 8 kHz.
 *
 * Audio in, 16-bit samples at any rate from 4800 a second up (fs / 1200 samples a bit, need not
 * be whole). The audio's sign always changes at a bit's edge (precoded FFSK, 600 and 1200 Hz at
 * 1200 baud), and its sign across that edge follows the line bit, so a line bit's soft value is
 * y = (the audio over the half bit before the edge) - (over the half bit after it), an integrate
 * and dump on the edge. The clock is a bank of RR_NPH phases a bit apart by T / RR_NPH, each
 * keeping a running mean of |y|: the bank's best phase is where the edges are (half a bit off, the
 * 600 Hz bits give 0), and it follows a drifting clock by moving to the next phase, a wrap adding
 * or dropping one soft value. Soft values are scaled to the best phase's mean |y| as 4 (clean:
 * +-4, as the K6's four chip samples a bit) and kept -7..7.
 *
 * Above that, as rangerx.c and modem.k6r.Receiver: the sync 1ACFFC1D by correlating the last 32
 * soft values (|c| >= RR_SYNC_MIN of 128; its sign says which way up the burst is; a better one by
 * RR_RESYNC_MARGIN in the first RR_RESYNC_BITS, or a better one of RR_SYNC_STRONG or more anywhere, starts
 * the packet again there: noise between bursts makes false syncs), the 2064 soft values
 * after it de-interleaved (24 rows of 86) and decoded by PHY G's K=7 Viterbi (133, 171, 8 zero
 * tail bits), then the 128-byte XL pager payload's CRC checked (de-whitened PN9, CRC-16/CCITT-FALSE
 * on bytes 0-125 against 126-127).
 *
 * No malloc, no OS, no floating point, no writable statics and no tables of pointers: a RAM
 * module (-fropi) can #include range_rx.c and keep the rr_state (about 11 KB, most of it the
 * Viterbi's survivors) in its scratch RAM.
 *
 * SPDX-License-Identifier: MIT */
#ifndef RANGE_RX_H
#define RANGE_RX_H

#include <stdint.h>

#define RR_NPH 16               /* clock phases a bit */
#ifndef RR_HIST
#define RR_HIST 128             /* audio kept (prefix sums), a power of 2 above 2 bits + 2 samples:
                                   128 to 48 kHz; a module at 8 kHz builds with 32 */
#endif
#define RR_SYNC 0x1ACFFC1Du
#define RR_SYNC_MIN 96          /* |correlation| of 32 soft values (128 from clean ones) */
#define RR_SYNC_STRONG 112      /* a sync this strong restarts a packet anywhere in it */
#define RR_RESYNC_BITS 160u
#define RR_RESYNC_MARGIN 16
#define RR_CODED 2064u
#define RR_STEPS 1032u
#define RR_PAYLOAD 128u

typedef struct {
    /* the clock */
    uint32_t T;                 /* samples a bit, Q16 */
    uint32_t n;                 /* samples in */
    uint32_t base;              /* the edge of this step's phase 0, Q16 samples (wraps) */
    int32_t dc;                 /* the audio's mean, Q8 (a slow high-pass) */
    uint32_t cum[RR_HIST];      /* cum[i % RR_HIST]: the sum of the first i samples (mod 2^32) */
    int32_t E[RR_NPH];          /* mean |y| at each phase, Q4 */
    int32_t y[RR_NPH];          /* this step's y at each phase */
    int32_t yp[RR_NPH];         /* the step before's */
    uint8_t ph;                 /* the phase the soft values come from */
    uint8_t warm;               /* steps seen, to 255 */
    /* the bit layer */
    int8_t win[32];             /* the last 32 soft values, a ring */
    uint8_t wpos;
    uint8_t collecting;
    int8_t sgn;                 /* the burst's way up */
    int16_t corr, best;         /* the packet's sync correlation, the best in its first bits */
    uint32_t got;               /* soft values of the packet so far */
    uint32_t bits;              /* soft values said, all told */
    uint32_t start;             /* `bits` at the packet's sync */
    int8_t soft[RR_CODED];      /* the packet's soft values, line order, > 0: bit 0 */
    /* the packet just decoded (rr_push returned 1) */
    uint8_t payload[RR_PAYLOAD];    /* as sent: whitened */
    int ok;                     /* its CRC checks */
    int errors;                 /* line bits whose sign its own code disagrees with (of 2064) */
    int ymean;                  /* mean |soft value| x 10 (40 from clean bits) */
    int pcorr;                  /* its sync's correlation (sign: the burst's way up) */
    uint32_t psync;             /* `bits` at its sync */
    /* the Viterbi's survivors: bit s of surv[t] = which predecessor of state s at step t */
    uint32_t surv[RR_STEPS][2];
} rr_state;

/* fs: samples a second (4800 to 65535) */
void rr_init(rr_state *r, unsigned fs);
/* Takes samples x[0..n-1] until a packet is decoded: returns 1 with *used = the samples taken
   (the packet's fields in r; call again with the rest), or 0 with *used = n. */
int rr_push(rr_state *r, const int16_t *x, unsigned n, unsigned *used);

/* the parts, for the tests */
void rr_decode(rr_state *r);    /* r->soft (line order) -> payload, ok, errors, ymean */
int rr_crc_ok(const uint8_t *payload);
unsigned rr_seq(const uint8_t *payload);   /* the pager header's seq (bytes 2-3, low 13 bits) */

#endif
