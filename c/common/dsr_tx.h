/* dsr_tx: DSotto raw frames, built on the radio one frame at a time, for the UV-380's HR_C6000
   physical layer mode (docs/uv380-dsotto-raw.md). A C port of the encoder half of modem/dsr.py,
   byte for byte the same (tests/test_uv380_dsr.py): the chip sends these bytes as 4FSK unchanged
   (dibit 01 +3, 00 +1, 10 -1, 11 -3).

   A frame is its sync (outer symbols only) then the coded bits of
       [type << 4 | counter >> 4, counter << 4 | meta >> 8, meta] [nsf sotto superframes] [CRC-16]
   (meta: 12 bits of the sender's base-40 callsign, chunk counter mod 4; CRC: M17's), under a
   K=7 tail-biting convolutional code (generators 0105 0127 0157 0167 0113 0173 octal, bit 0 =
   the newest input bit; the first four are the rate 1/4 code), coded bit i at A i mod N, XOR
   PN9 (x^9 + x^5 + 1, seed 0x1FF, the same every frame).

     DSRT_120  120 ms, 144 bytes (four C6000 slots): 24-symbol sync, 3 superframes, rate 1/6
     DSRT_40    40 ms,  48 bytes: 16-symbol sync, 1 superframe, rate 1/4

   Nothing here allocates; the largest buffer on the stack is 23 bytes.
   SPDX-License-Identifier: MIT */
#ifndef DSR_TX_H
#define DSR_TX_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DSRT_SF 6u                      /* a sotto 1200 superframe, 40 ms */
#define DSRT_MAXFRAME 144u
#define DSRT_PRE 48u                    /* preamble: 40 ms of 0x77 (+3 -3 ...) */
#define DSRT_POST 2u                    /* after the end frame: 0x77 0x77 */
#define DSRT_T_SOTTO1200 0x1u
#define DSRT_T_SOTTO2400 0x2u           /* reserved */
#define DSRT_T_END 0xFu

enum { DSRT_40 = 0, DSRT_120 = 1 };

typedef struct {
    uint8_t fmt;                        /* DSRT_40 or DSRT_120 */
    uint8_t nsf, rate, nsync;           /* superframes a frame, 1/rate, sync bytes */
    uint16_t frame, ninfo, ncoded, a;   /* bytes a frame, info bits, coded bits, interleaver A */
    uint8_t call[6];                    /* base-40 callsign */
    uint8_t counter;                    /* the next frame's counter */
} dsrt;

/* 0, or -1 (an unknown format or a bad callsign) */
int dsrt_init(dsrt *t, unsigned fmt, const char *call);
/* the next frame into out (t->frame bytes): type, nsf superframes (NULL = zeros, for an end
   frame); the counter goes up by one */
void dsrt_frame(dsrt *t, unsigned type, const uint8_t *sf, uint8_t *out);

/* A whole stored message, pulled a slot at a time: the preamble, a frame for each nsf superframes
   (the last one padded with the last superframe), an end frame, 0x77 0x77 (modem/dsr.py
   tx_stream). The superframes must stay put until it is all out. */
typedef struct {
    dsrt t;
    const uint8_t *sf;
    unsigned n, frames, frame;          /* superframes; data frames; the next frame to build */
    uint8_t buf[DSRT_MAXFRAME];
    unsigned pos, len;                  /* bytes of buf handed out, of len */
    unsigned stage;                     /* 0 preamble, 1 frames, 2 post, 3 done */
} dsrt_msg;

int dsrt_msg_begin(dsrt_msg *m, unsigned fmt, const uint8_t *sf, unsigned n, const char *call);
unsigned dsrt_msg_read(dsrt_msg *m, uint8_t *out, unsigned max);
unsigned dsrt_msg_bytes(const dsrt_msg *m);

#ifdef __cplusplus
}
#endif

#endif
