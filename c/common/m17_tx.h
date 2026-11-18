/* m17_tx: M17 (m17project.org) frames, built on the radio one 48-byte frame at a time, for the
   UV-380's HR_C6000 physical layer mode (docs/uv380-multimode.md). A C port of the encoder half
   of modem/m17.py, byte for byte the same (tests/test_uv380_framers.py): the chip sends these bytes as
   4FSK unchanged (dibit 01 +3, 00 +1, 10 -1, 11 -3 is M17's map).

   A frame is 2 sync bytes then 368 bits: payload, K=5 rate 1/2 code (G1 0x19, G2 0x17, 4 flush
   bits), punctured (LSF P1, stream and BERT P2, packet P3), the quadratic interleaver
   (45 i + 92 i^2 mod 368), XOR the decorrelator. Nothing here allocates; the largest buffer on
   the stack is 48 bytes.

   sotto (provisional, docs/uv380-multimode.md "Framers and the sotto payload"): an M17 stream of
   TYPE M17T_TYPE_STREAM_DATA with the tag "SOTTO 1200" in META, and in each stream frame's 16
   bytes two sotto 1200 superframes, M17's CRC-16 of the two, a control byte and 0
   (m17t_sotto_payload). m17t_sotto_* streams a whole transmission into the 36-byte slots the
   chip takes.
   SPDX-License-Identifier: MIT */
#ifndef M17_TX_H
#define M17_TX_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define M17T_FRAME 48u                  /* bytes a frame: 40 ms, 192 symbols */
#define M17T_LSF 30u                    /* link setup frame bytes (CRC included) */
#define M17T_PRE_LSF 0x77u              /* preamble before an LSF: +3 -3 ... */
#define M17T_PRE_BERT 0xDDu             /* before BERT frames: -3 +3 ... */
#define M17T_TYPE_PACKET_DATA 0x0002u
#define M17T_TYPE_STREAM_VOICE 0x0005u  /* Codec2 3200 */
#define M17T_TYPE_STREAM_DATA 0x0003u   /* sotto */
#define M17T_SOTTO_SF 6u                /* a sotto 1200 superframe, 40 ms */
#define M17T_SOTTO_RATE_1200 0x10u      /* payload byte 14, bits 7-4 */
#define M17T_SOTTO_REPEAT 0x01u         /* byte 14 bit 0: b is the superframe before a */

/* M17's CRC: polynomial 0x5935, initial 0xFFFF */
uint16_t m17t_crc(const uint8_t *d, unsigned n);

/* base 40, big endian, 6 bytes; "", "ALL" and "@ALL" are broadcast (FF x 6). 0, or -1 (a
   character outside " A-Z0-9-/." or longer than 9: out is broadcast) */
int m17t_callsign(const char *s, uint8_t out[6]);

/* the 30-byte LSF: dst, src, type (big endian), meta (14 bytes, NULL = zeros), CRC */
void m17t_lsf(uint8_t l30[M17T_LSF], const char *src, const char *dst, uint16_t type,
              const uint8_t *meta);

void m17t_fill(uint8_t out[M17T_FRAME], uint8_t b);       /* a preamble: 48 x b */
void m17t_eot(uint8_t out[M17T_FRAME]);                   /* 55 5D x 24 */
void m17t_frame_lsf(const uint8_t l30[M17T_LSF], uint8_t out[M17T_FRAME]);
/* a stream frame: fn (15 bits; its LICH is sixth fn % 6 of the LSF), 16 payload bytes, last
   sets the frame number's top bit */
void m17t_frame_stream(const uint8_t l30[M17T_LSF], uint16_t fn, const uint8_t pay[16], int last,
                       uint8_t out[M17T_FRAME]);
/* a packet frame: n (<= 25) bytes of chunk, eof, counter (the frame's number, or on the last
   frame the bytes used) */
void m17t_frame_packet(const uint8_t *chunk, unsigned n, int eof, unsigned counter,
                       uint8_t out[M17T_FRAME]);
/* a BERT frame: 197 bits of PRBS9 (x^9 + x^5 + 1) from *prbs (seed 1), which it advances */
void m17t_frame_bert(uint16_t *prbs, uint8_t out[M17T_FRAME]);

/* a stream frame's 16 bytes for sotto 1200: a, b (6 bytes each), CRC-16 of the 12, control
   (M17T_SOTTO_RATE_1200, | M17T_SOTTO_REPEAT when b repeats the one before a), 0 */
void m17t_sotto_payload(const uint8_t a[6], const uint8_t b[6], int repeat, uint8_t out[16]);

/* A whole sotto transmission, pulled a slot at a time: preamble, LSF, stream frames, EOT
   (modem/m17.py tx_sotto). repeat 0: two new superframes a 40 ms frame (a stored message goes
   out in half its length); 1: real time, one new and the one before. The superframes must stay
   put until the transmission ends. */
typedef struct {
    const uint8_t *sf;                  /* n superframes, 6 bytes each */
    unsigned n, repeat;
    uint8_t l30[M17T_LSF];
    unsigned frame, frames;             /* the next frame to build, of frames (all of them) */
    uint8_t buf[M17T_FRAME];
    unsigned pos;                       /* bytes of buf already handed out */
} m17t_sotto;

/* 0, or -1 (no superframes, or a bad callsign: src AG7EW style, dst "" = broadcast) */
int m17t_sotto_begin(m17t_sotto *t, const uint8_t *sf, unsigned n, const char *src,
                     const char *dst, int repeat);
/* up to max next bytes of the transmission into out: how many (0 once it is all out) */
unsigned m17t_sotto_read(m17t_sotto *t, uint8_t *out, unsigned max);
unsigned m17t_sotto_bytes(const m17t_sotto *t);           /* the whole transmission's length */

#ifdef __cplusplus
}
#endif

#endif
