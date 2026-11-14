/* PHY F receiver for a radio that hears the audio itself: the K6 voice pager's plain FSK packets
 * (modem/pager.py's Air, modem/fsk.py's profile F; the senders are ffsk_tx.h and the K6's chip),
 * taken out of FM receiver audio at 8 kHz. The UV-380 first, beside range_rx.h in PAGER mode's
 * listener (Jeff 11-14: "As well as receiving FSK").
 *
 * On air: phase-continuous FFSK at 1800 baud, bit 1 = mark (1200 Hz), bit 0 = space (1800 Hz),
 * MSB first: 0x55 preamble, the sync 07 6B 3E 36, a 64 (L) or 128 (XL) byte payload, whitened PN9,
 * CRC-16/CCITT-FALSE in its last two bytes. No coding: each bit stands alone.
 *
 * The audio is mixed down by 1500 Hz (the tones' middle) and low-passed (11 taps, 1.3 kHz), and
 * the frequency is read off the turn from one sample to the next (cross product over power): mark
 * is -300 Hz, space +300 Hz. A bit's soft value is that summed over a bit (4.44 samples), from
 * prefix sums at fractional edges. The clock is a bank of FR_NPH phases T / FR_NPH apart, each
 * keeping a running mean of |soft| (the eye is widest at its best phase); every phase looks for the
 * sync in its own hard bits (at most FR_SYNC_ERR of 32 wrong). The phase that finds it, or a better
 * one finding the same sync within a bit, takes the packet's bits (a perfect sync later takes over
 * from one with errors, which noise makes now and then). Meanwhile a Gardner timing error
 * at each change of bit (the soft value of the phase half a bit off, on the edge, times the change)
 * moves it to the next phase once FR_TED of them agree, for a sender whose clock is off.
 *
 * Fixed at 8000 samples a second. No malloc, no OS, no floating point, no writable statics.
 *
 * SPDX-License-Identifier: MIT */
#ifndef FSK_RX_H
#define FSK_RX_H

#include <stdint.h>

#define FR_FS 8000u
#define FR_BAUD 1800u
#define FR_NPH 8                /* clock phases a bit */
#define FR_TAPS 11
#define FR_HIST 16              /* prefix sums kept, a power of 2 over a bit + 2 samples */
#define FR_SYNC 0x076B3E36u
#define FR_SYNC_ERR 2           /* sync bits that may be wrong */
#define FR_MAX 128              /* payload bytes (XL) */
#define FR_TED 2                /* timing error, in mean |soft|s, that moves the clock a phase */

typedef struct {
    uint32_t n;                 /* samples in */
    uint32_t ph;                /* the mixer's place, 0..15 (1500 Hz at 8 kHz repeats every 16) */
    int32_t mi[FR_TAPS], mq[FR_TAPS];  /* the mixed audio, a ring */
    uint32_t mpos;
    int32_t pi, pq;             /* the last filtered sample */
    uint32_t cum[FR_HIST];      /* cum[i % FR_HIST]: the sum of the first i frequency values */
    /* the clock bank */
    uint32_t next[FR_NPH];      /* the end of each phase's next bit, Q16 samples (wraps) */
    uint32_t k[FR_NPH];         /* bits each phase has said */
    uint32_t reg[FR_NPH];       /* each phase's last 32 hard bits */
    int32_t e[FR_NPH];          /* each phase's mean |soft| */
    int32_t last[FR_NPH];       /* each phase's last soft value */
    /* the packet being taken */
    int collecting;
    unsigned cph;               /* the phase it comes from */
    uint32_t cpos;              /* the last bit taken: k * FR_NPH + phase */
    uint32_t spos;              /* the sync's */
    unsigned serr;              /* the sync's wrong bits */
    unsigned got;               /* bits taken */
    int ted;                    /* the timing error, summed (bang-bang) */
    uint8_t buf[FR_MAX];
    /* the packet just taken (fr_push returned 1) */
    uint8_t payload[FR_MAX];    /* as sent: whitened */
    unsigned len;               /* 64 or 128 */
    int ok;                     /* its CRC checks (64: checked at 512 bits, else 128) */
    uint32_t psync;             /* the sync's position, bits * FR_NPH */
    int pe;                     /* the phase's mean |soft| then (the eye) */
} fr_state;

void fr_init(fr_state *r);
/* Takes 8 kHz samples x[0..n-1] until a packet ends: returns 1 with *used = the samples taken
   (the packet in r->payload, r->len, r->ok; call again with the rest), or 0 with *used = n. */
int fr_push(fr_state *r, const int16_t *x, unsigned n, unsigned *used);

#endif
