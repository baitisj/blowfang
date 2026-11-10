/* The transmit side of the K6 voice-pager packet format (modem/pager.py has the spec; the K6's
 * receiver is c/k6/sotto_pager.c), in portable C for radios that send sotto messages
 * themselves: the UV-380 first (radios/uv380). It packs 6-byte sotto frames into L (64-byte) or
 * XL (128-byte) payloads, interleaved, CRC'd and PN9-whitened exactly as modem.pager.Profile.pack
 * does, and frames them for the air as modem.pager.Air does (0x55 lead-in and preambles, sync
 * 07 6B 3E 36, MSB first). The bits then go to any modulator (c/common/ffsk_tx.h: FFSK
 * 1200/1800 Hz, the K6's F profile).
 *
 * No malloc, no OS, no floating point. Packets are built one at a time on demand from the frame
 * buffer, so a radio needs only one 128-byte packet buffer, whatever the message length.
 *
 * SPDX-License-Identifier: MIT */
#ifndef PAGER_TX_H
#define PAGER_TX_H

#include <stdint.h>

#define PT_FRAME_BYTES 6
#define PT_HDR 6
#define PT_MAX_PAYLOAD 128

/* byte 0 of L and XL: which codec the frames are (modem/pager.py CODECS) */
#define PT_MAGIC_2400 0xA7      /* sotto v3.7, 20 ms per frame */
#define PT_MAGIC_1200 0xA9      /* sotto 1200, 40 ms per frame */
#define PT_MAGIC_600 0xAC       /* sotto 600, 80 ms */
#define PT_MAGIC_400 0xAD       /* sotto 400, 120 ms */
#define PT_PARITY 0x10          /* magic | PT_PARITY: an interleave group's XOR parity packet */

typedef struct {
    uint8_t payload;            /* bytes per packet: 64 (L) or 128 (XL) */
    uint8_t slots;              /* sotto frames per packet: 8 (L) or 20 (XL) */
    uint8_t magic;              /* PT_MAGIC_* */
    uint8_t depth;              /* packets per interleave group: 8, 4, 2 or 1 */
    uint8_t short_last;         /* a partial last group takes only the packets it fills */
    uint8_t parity;             /* one XOR parity packet after each group */
} pt_profile;

/* The usual profiles: depth 8, short last group on, no parity. */
#define PT_PROFILE_L(magic) {64, 8, (magic), 8, 1, 0}
#define PT_PROFILE_XL(magic) {128, 20, (magic), 8, 1, 0}

unsigned pt_crc16(const uint8_t *p, unsigned n);          /* CRC-16/CCITT-FALSE */
uint32_t pt_crc32(const uint8_t *p, unsigned n);          /* zlib's, for msg_id */
void pt_whiten(uint8_t *p, unsigned n);                   /* XOR PN9 (seed 0x1FF) in place */

/* msg_id as modem.k6 picks it: the low byte of zlib.crc32 of the frames. */
uint8_t pt_msg_id(const uint8_t *frames, unsigned total);

/* Data packets (seqs) in a message of `total` frames, and packets on air for one pass (data plus
 * parity). 0 when the message doesn't fit the format (65535 frames or more: 0xFFFF in the
 * header marks an open stream; or over 8192 packets). */
unsigned pt_n_packets(const pt_profile *pr, unsigned total);
unsigned pt_air_packets(const pt_profile *pr, unsigned total);

/* Builds packet i (0 .. pt_air_packets-1, in transmit order: each group's data packets, then
 * its parity packet when pr->parity) of the message `frames` (total frames, 6 bytes each) into
 * out (pr->payload bytes, whitened, ready for the air). Returns pr->payload, or 0 for a bad i. */
unsigned pt_packet(const pt_profile *pr, uint8_t msg_id, const uint8_t *frames, unsigned total,
                   unsigned i, uint8_t *out);

/* ---- framing for the air (modem.pager.Air) --------------------------------------------------
 * lead_bytes of 0x55 after keying, then per packet pre bytes of 0x55 (first_pre for packet 0,
 * later_pre after it, extra_second more before packet 1), the sync word, the payload; tail_bytes
 * of 0x55 before unkeying. pt_air_defaults fills these as modem.k6 tx does for the profile and
 * baud rate. */
typedef unsigned (*pt_source)(void *ctx, unsigned i, uint8_t *out);   /* packet i -> its length, 0: no more */

typedef struct {
    unsigned lead_bytes, first_pre, later_pre, extra_second, tail_bytes;
    pt_source source;
    void *ctx;
    /* state */
    uint8_t buf[PT_MAX_PAYLOAD];
    unsigned pkt, len, phase, pos, byte, bit;
} pt_air;

void pt_air_defaults(pt_air *a, unsigned payload, unsigned baud, pt_source source, void *ctx);
void pt_air_start(pt_air *a);

/* The next bit on air (MSB first), or -1 when the burst is over. */
int pt_air_bit(pt_air *a);

/* Bits in the whole burst for n packets of `payload` bytes (for air-time estimates). */
unsigned long pt_air_bits(const pt_air *a, unsigned n, unsigned payload);

/* A pt_source over one message: packets 0 .. pt_air_packets-1, `passes` times over. */
typedef struct {
    const pt_profile *pr;
    const uint8_t *frames;
    unsigned total, passes;
    uint8_t msg_id;
} pt_message;

unsigned pt_message_source(void *ctx, unsigned i, uint8_t *out);

#endif
