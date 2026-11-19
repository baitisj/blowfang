/* A YSF call caught frame by frame for a parrot (firmware/uv380-openrtx/mod/c6ysftx_echo.c): each
 * frame ysf_rx reads is kept as the 120 bytes it was sent as (its 480 symbols decided hard from the
 * levels ysf_rx kept), so the call can go back out unchanged: a DN call's AMBE voice, DCH and
 * FICH included, with no vocoder. The keeper reports only counts and the caller's callsign.
 * SPDX-License-Identifier: MIT */
#ifndef YSF_ECHO_H
#define YSF_ECHO_H
#include <stdint.h>
#include "ysf_rx.h"

#define YE_FRAME 120u                   /* bytes a frame: 480 symbols */

typedef struct {
    uint8_t *frames;                    /* max x YE_FRAME bytes */
    unsigned max, n;                    /* room, frames kept */
    unsigned dropped;                   /* frames past max */
    unsigned hdr, term, voice, other;   /* kept, by kind (voice: DN / VW comms frames) */
    unsigned gaps;                      /* frames missing between two kept ones (by their time) */
    int ended;                          /* a terminator came */
    uint32_t last_at;
    char src[11];                       /* the header's source callsign, "?" until one decodes */
} ye_state;

void ye_init(ye_state *e, uint8_t *frames, unsigned max);
/* the frame ysf_rx just read (its symbols in r->sym, r->inv set), as bytes */
void ye_bytes(const ysf_rx *r, uint8_t out[YE_FRAME]);
/* keep the frame f ysf_push just gave: 1 if kept. The last place is kept for the terminator, so
   a call longer than the room goes back as its start and its end. */
int ye_put(ye_state *e, const ysf_rx *r, const ysf_frame *f);

#endif
