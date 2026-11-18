/* YSF (C4FM) receiver for 8 kHz discriminator audio: the UV-380's HR_C6000 FM tap with no hardware
 * mod (docs/uv380-m17-rx.md, docs/uv380-multimode.md milestone 5). The C version of modem/ysf.py's
 * demodulator and decoder, for a RAM module, and the receive half of ysf_tx.c.
 *
 * Audio in, 16-bit at 8000 samples a second, +3 symbols positive (the UV-380's tap reads +3
 * negative: set YSF_INVERT or negate first). The front end is m17_rx.c's: a slow DC follower,
 * then each sample makes three at 24 kHz through one RRC (alpha 0.2, YSF's and the chip's roll-off,
 * +-6 symbols, 5 samples a symbol). Every 24 kHz sample the last 20 symbol-spaced samples are
 * correlated with the 40-bit sync (D4 71 C9 63 4D); a score of YSF_SYNC_MIN at its local peak
 * starts a frame, and its 480 symbols are kept (as 16-bit matched-filter samples) as they arrive.
 * Once a frame is read the next sync is looked for at its 100 ms point +-YSF_TRACK samples,
 * accepted down to YSF_SYNC_KEEP; YSF_MISSES in a row and it hunts again.
 *
 * A frame counts when its FICH decodes (K=5 Viterbi, four Golay(24,12) words, CRC-16): noise that
 * matches the sync does not get through all three. Once locked, a frame whose FICH fails is still
 * read as the next frame of the same kind when that kind carries its own check (sotto VW: a CRC-8
 * a superframe). Payloads decoded: header and terminator (CSD1: dest + src), data full rate
 * (DCH1 and DCH2) and the sotto softmodem's sotto VW (five 6-byte superframes, tail-biting K=7
 * (133, 171, 165) at rate 7/18, CRC-8 each). DN voice frames give their FICH only.
 *
 * Soft bits come straight from the kept symbols (MSB: the level; LSB: 2 - |level|), so no frame
 * of soft values is held. Floating point (the Cortex-M4F has an FPU); no malloc, no writable
 * statics, no tables of pointers: a RAM module (-fropi) can #include ysf_rx.c and keep ysf_rx
 * (about 5 KB) in scratch.
 * SPDX-License-Identifier: MIT */
#ifndef YSF_RX_H
#define YSF_RX_H

#include <stdint.h>

#define YSF_SPS 5                       /* 24 kHz samples a symbol */
#define YSF_NTAP 61                     /* RRC +-6 symbols */
#define YSF_YN 256                      /* 24 kHz matched-filter samples kept (a power of 2) */
#define YSF_NSYNC 20                    /* sync symbols */
#define YSF_NSYM 480                    /* symbols a frame: 100 ms */
#define YSF_FRAME 2400                  /* 100 ms at 24 kHz */
#define YSF_WRAP 48                     /* tail-biting Viterbi: steps run on either side */
#define YSF_YSCALE 0.5f
#ifndef YSF_SYNC_MIN
#define YSF_SYNC_MIN 0.85f
#endif
#ifndef YSF_SYNC_KEEP
#define YSF_SYNC_KEEP 0.50f
#endif
#ifndef YSF_TRACK
#define YSF_TRACK 3
#endif
#ifndef YSF_MISSES
#define YSF_MISSES 3
#endif
#ifndef YSF_INVERT
#define YSF_INVERT 0
#endif

enum { YSF_FI_HEADER = 0, YSF_FI_COMMS = 1, YSF_FI_TERM = 2, YSF_FI_TEST = 3 };
enum { YSF_DT_VD1 = 0, YSF_DT_DATA_FR = 1, YSF_DT_VD2 = 2, YSF_DT_VOICE_FR = 3 };
/* what data[] holds */
enum { YSF_PAY_NONE = 0, YSF_PAY_CSD = 1, YSF_PAY_DATA_FR = 2, YSF_PAY_SOTTO = 3 };

typedef struct {
    int fich_ok;                        /* the FICH decoded (0: read on as the last frame's kind) */
    uint8_t fich[4];                    /* its four bytes */
    uint8_t fi, fn, ft, dt, sotto, dgid;
    int pay;                            /* YSF_PAY_* */
    uint8_t ok;                         /* CSD / data FR: bit 0 field 1 CRC good, bit 1 field 2;
                                           sotto: bit k superframe k's CRC-8 good */
    uint8_t data[40];                   /* CSD: 20 + 20 (dest 10, src 10, then CSD2); data FR:
                                           DCH1 + DCH2; sotto: five superframes (30) */
    uint32_t at;                        /* 24 kHz sample of the sync's first symbol */
    float score;                        /* sync correlation squared (1.0 = perfect) */
    float gain;                         /* sync fit: one level unit in input units */
    float metric;                       /* FICH Viterbi: share of soft weight disagreeing */
} ysf_frame;

typedef struct {
    int16_t x[32];
    unsigned xi;
    float dc;
    int16_t y[YSF_YN];
    uint32_t yn;                        /* 24 kHz samples made */
    int state;                          /* 0 hunt, 1 a peak to confirm, 2 frame to read, 3 locked */
    uint32_t cand;                      /* this frame's sync start */
    float cscore;
    uint32_t next;                      /* locked: the next sync's expected start */
    int misses;
    int locked;
    uint8_t last[4];                    /* the last good FICH (fi, dt, sotto, ft) */
    uint8_t last_fn;
    int16_t sym[YSF_NSYM];
    unsigned nsym;
    float inv;                          /* 1 / gain while a frame is decoded */
    uint8_t pn[90];                     /* sotto VW's PN9 (seed 0x1FF), 720 bits */
    uint8_t white[20];                  /* the CSD whitening (PN9 seed 0x1C9) */
    union {
        uint64_t k7[280 + 2 * YSF_WRAP];  /* the sotto VW decoder's survivors */
        uint16_t k5[184];                 /* the FICH's and the CSD's */
    } surv;
    uint32_t frames, lost, bad;         /* counts: frames, locks lost, FICH failures read on */
} ysf_rx;

void ysf_init(ysf_rx *r);
/* push one 8 kHz sample; 1 when *out holds a new frame */
int ysf_push(ysf_rx *r, int16_t s, ysf_frame *out);
/* CRC-16 CCITT, initial 0, inverted (the FICH's and the CSD's) */
uint16_t ysf_crc(const uint8_t *d, unsigned n);

#endif
