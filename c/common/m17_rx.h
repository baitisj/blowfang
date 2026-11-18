/* M17 receiver for 8 kHz discriminator audio: the UV-380's HR_C6000 FM tap with no hardware mod
 * (docs/uv380-m17-rx.md). The C version of modem/m17.py's demodulator and decoder, for a RAM module.
 *
 * Audio in, 16-bit at 8000 samples a second, +3 symbols positive (the UV-380's tap reads +3
 * negative: set M17_INVERT or negate first). A slow DC follower takes off the carrier offset.
 * Each sample makes three at 24 kHz through one RRC (alpha 0.5, +-4 symbols, 5 samples a symbol):
 * the x3 interpolator and the matched filter in one. Every 24 kHz sample the last 8 symbol-spaced
 * samples are correlated with the five syncwords (LSF, stream, packet, BERT, EOT); a score of
 * M17_SYNC_MIN (of 1.0) at its local peak starts a frame. Once a frame is decoded the next sync is
 * looked for at its 40 ms point +-M17_TRACK samples (follows the far end's clock), accepted down
 * to M17_SYNC_KEEP; M17_MISSES in a row and it hunts again. A frame counts only if its Viterbi
 * path disagrees with at most M17_Q_MIN (hunting) or M17_Q_KEEP (locked) of the soft weight: noise
 * matches a sync now and then, its frames decode to nothing.
 * A frame's 192 symbols are scaled by the gain the sync fits (least squares), turned to soft bits
 * (MSB: the level; LSB: 2 - |level|), decorrelated, de-interleaved, depunctured and decoded by a
 * soft K=5 Viterbi; the LICH by Golay(24,12); LSF and packet CRCs checked.
 *
 * Floating point (the UV-380's Cortex-M4F has an FPU); no malloc, no writable statics, no tables of
 * pointers: a RAM module (-fropi) can #include m17_rx.c and keep m17_rx (about 6.3 KB) in scratch.
 * SPDX-License-Identifier: MIT */
#ifndef M17_RX_H
#define M17_RX_H

#include <stdint.h>

#define M17_SPS 5                       /* 24 kHz samples a symbol */
#define M17_NTAP 41                     /* RRC +-4 symbols */
#define M17_YN 1024                     /* 24 kHz matched-filter samples kept (a power of 2, > 1000) */
#define M17_FRAME 960                   /* 40 ms at 24 kHz */
#define M17_YSCALE 0.5f                 /* matched-filter samples kept as int16 at this scale */
#ifndef M17_SYNC_MIN
#define M17_SYNC_MIN 0.90f
#endif
#ifndef M17_SYNC_KEEP
#define M17_SYNC_KEEP 0.60f
#endif
#ifndef M17_TRACK
#define M17_TRACK 3
#endif
#ifndef M17_MISSES
#define M17_MISSES 3
#endif
#ifndef M17_Q_MIN
#define M17_Q_MIN 0.010f                /* a frame found by hunting counts only this clean (an LSF
                                           with a good CRC always counts); noise frames >= 0.015 */
#endif
#ifndef M17_Q_KEEP
#define M17_Q_KEEP 0.060f               /* once locked */
#endif
#ifndef M17_INVERT
#define M17_INVERT 0
#endif

enum { M17_LSF = 0, M17_STREAM = 1, M17_PACKET = 2, M17_BERT = 3, M17_EOT = 4, M17_KINDS = 5 };

typedef struct {
    int kind;
    int ok;                             /* LSF / packet: CRC good; stream: LICH good; BERT: 1 */
    uint32_t at;                        /* 24 kHz sample of the sync's first symbol */
    float score;                        /* sync correlation squared (1.0 = perfect) */
    float gain;                         /* sync fit: one level unit in input units */
    float metric;                       /* Viterbi: share of soft weight disagreeing (0 = clean) */
    uint8_t data[30];                   /* LSF 30, stream fn(2)+payload(16), packet 26, BERT 25 */
    uint8_t lich[6];                    /* stream: the LICH's 48 data bits (5 LSF bytes + index) */
} m17_frame;

typedef struct {
    float h[M17_NTAP];
    int16_t y[M17_YN];
    int16_t x[16];
    unsigned xi;
    float dc;
    uint32_t yn;                        /* 24 kHz samples made */
    int state;                          /* 0 hunt, 1 a peak to confirm, 2 frame to read, 3 locked */
    uint32_t cand;                      /* candidate sync start */
    float cscore;
    int ckind;
    uint32_t next;                      /* locked: the next sync's expected start */
    int misses;
    int locked;
    uint16_t surv[248];
    float pm[16];
    float soft[488];
    float raw[368];
    uint32_t frames, lost;              /* counts */
} m17_rx;

void m17_init(m17_rx *r);
/* push one 8 kHz sample; 1 when *out holds a new frame */
int m17_push(m17_rx *r, int16_t s, m17_frame *out);
uint16_t m17_crc(const uint8_t *d, unsigned n);
/* 6-byte base-40 callsign -> text (up to 9 chars + NUL); "ALL" for broadcast */
void m17_callsign(const uint8_t *b, char *out);

#endif
