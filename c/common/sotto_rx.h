/* sotto_rx: a sotto 1200 message put back together from what the UV-380's M17 and YSF receivers
 * (m17_rx.c, ysf_rx.c) decode, for PAGER mode's M17 / YSF receive (docs/uv380-multimode.md,
 * milestone 5): the receive half of m17_tx.c's and ysf_tx.c's sotto framing.
 *
 * Each superframe (6 bytes, 40 ms) goes to its place in the message:
 *   M17 stream frames: payload = a(6) b(6) CRC-16(a b) ctrl 0 (m17_tx.h m17t_sotto_payload);
 *     ctrl bits 7-4 the rate (1: sotto 1200), bit 0 set: b repeats the superframe before a. So
 *     frame fn holds superframes 2fn and 2fn+1 (bit 0 clear) or fn and fn-1 (set). A frame whose
 *     CRC fails is passed over; the last frame (fn bit 15) or an EOT ends the message.
 *   YSF sotto VW frames (ysf_rx's YSF_PAY_SOTTO): five superframes, each with its own CRC-8;
 *     frames are 100 ms apart on air, so a frame's place comes from its sync time (ysf_frame.at,
 *     24 kHz samples) against the first sotto frame's, which also places frames after lost ones.
 *     The terminator ends the message.
 * A superframe that never came is filled with the one before it (the first with the next one
 * that came), as pager_rx.c's prx_conceal does, so the decoder holds the sound over a gap.
 *
 * No malloc, no writable statics, no floating point: the caller owns the buffers, and a RAM
 * module (-fropi) can #include sotto_rx.c. SRX_NO_RADIO leaves out srx_m17 and srx_ysf (and
 * the UV-380 receivers' headers): the K6's ysfrx (c/k6/mod/ysfdec.inc) places its own frames.
 * SPDX-License-Identifier: MIT */
#ifndef SOTTO_RX_H
#define SOTTO_RX_H

#include <stdint.h>

#ifndef SRX_NO_RADIO
#include "m17_rx.h"
#include "ysf_rx.h"
#endif

#define SRX_SF 6u                       /* bytes a superframe */
#define SRX_YSF_FRAME 2400u             /* a YSF frame at ysf_rx's 24 kHz */

typedef struct {
    uint8_t *frames;                    /* max x SRX_SF bytes, the caller's */
    uint8_t *have;                      /* max / 8 + 1 bytes, the caller's */
    unsigned max;
    int based;                          /* a superframe has come: base holds its place */
    int32_t base;                       /* the message's superframe 0 (the first index seen) */
    uint32_t at0;                       /* YSF: the first sotto frame's sync time */
    unsigned n;                         /* superframes spanned: the highest place + 1 */
    unsigned got;                       /* superframes received (each place once) */
    unsigned bad;                       /* M17 payloads failing CRC-16, YSF superframes failing CRC-8 */
    unsigned over;                      /* superframes past max or before the start, dropped */
    int ended;                          /* the last frame, an EOT or a terminator came */
} srx_state;

void srx_init(srx_state *s, uint8_t *frames, uint8_t *have, unsigned max);
/* superframe sf at place idx (any origin: the first one seen is the message's 0); 1 if new */
int srx_put(srx_state *s, int32_t idx, const uint8_t *sf);
#ifndef SRX_NO_RADIO
/* an M17 frame: superframes it gave (0 for LSF, packet, BERT, EOT or a bad payload) */
int srx_m17(srx_state *s, const m17_frame *f);
/* a YSF frame: superframes it gave */
int srx_ysf(srx_state *s, const ysf_frame *f);
#endif
/* fills the places that never came; returns how many were missing */
unsigned srx_conceal(srx_state *s);
/* M17's CRC-16 (polynomial 0x5935, initial 0xFFFF) */
uint16_t srx_crc16(const uint8_t *d, unsigned n);

#endif
