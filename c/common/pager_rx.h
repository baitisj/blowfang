/* The receive side of the K6 voice-pager packet format (pager_tx.h is the sender, modem/pager.py
 * the spec): puts a message back together from its packets as they come off the air, in any order,
 * with some missing, for a radio that plays what it hears (the UV-380 first: PHY R packets from
 * c/common/range_rx.c, then its sotto 1200 decoder on the speaker).
 *
 * Each payload (whitened, as on air) is de-whitened and its CRC checked; then its header says the
 * codec (magic), the message (msg_id), the packet (seq), the interleave depth and the message's
 * length in frames; its frames go where pager_tx's interleave took them from. A packet of another
 * msg_id starts a new message. Parity packets and open streams (total 0xFFFF) are passed over.
 *
 * No malloc, no OS, no floating point: the caller owns the frame buffer.
 *
 * SPDX-License-Identifier: MIT */
#ifndef PAGER_RX_H
#define PAGER_RX_H

#include <stdint.h>

#include "pager_tx.h"

typedef struct {
    uint8_t *frames;            /* max frames x 6 bytes, the caller's */
    uint8_t *have;              /* max / 8 + 1 bytes, the caller's: bit f set once frame f came */
    unsigned max;
    /* the message being put together (active) */
    int active;
    uint8_t magic, msg_id;
    unsigned total;             /* its frames (from the header) */
    unsigned got;               /* of them received */
    unsigned packets;           /* packets that went into it */
    unsigned bad;               /* payloads passed over since prx_init (CRC, magic, too long) */
} prx_state;

/* frames: max x PT_FRAME_BYTES bytes; have: max / 8 + 1 bytes */
void prx_init(prx_state *r, uint8_t *frames, uint8_t *have, unsigned max);
/* Forgets the message being put together. */
void prx_reset(prx_state *r);

#define PRX_BAD (-1)            /* CRC wrong, not sotto, parity, an open stream, too long */
#define PRX_OLD 0               /* checked, frames already had (or none new) */
#define PRX_NEW 1               /* checked, new frames */
#define PRX_DONE 2              /* checked, and the message is now whole */

/* One payload of n bytes (64: L, 128: XL), whitened as on air. Returns PRX_*. */
int prx_packet(prx_state *r, const uint8_t *payload, unsigned n);

/* Fills each frame not received with the one before it (the first with the next one received),
 * so the decoder holds the sound over a gap. Returns the frames that were missing. */
unsigned prx_conceal(prx_state *r);

#endif
