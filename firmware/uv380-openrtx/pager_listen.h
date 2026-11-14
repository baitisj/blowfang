/* pager_listen: PAGER mode's receiver on the UV-380 (Jeff 11-14: "get the synthesizer ported to
   the HT"). While the radio sits in receive, the FM receiver's audio comes off the C6000's codec
   on I2S3 at 8 kHz (mod/rxaudio.h's rx_start: register 26 and PD9 route it there, no hardware
   mod), PHY R packets are taken out of it (c/common/range_rx.c, as the rangerx module, 42/42 on
   air 11-13), and PHY F (FSK) packets too (c/common/fsk_rx.c; Jeff 11-14: "As well as receiving
   FSK"), and the message is put back together from them (c/common/pager_rx.c). A sotto 1200
   message that is whole, or three quarters there and quiet for PL_GAP_MS, is handed over for
   the speaker. Receive only: it touches no RF register. On anything but the MD-UV3x0 it does
   nothing. SPDX-License-Identifier: MIT */
#ifndef PAGER_LISTEN_H
#define PAGER_LISTEN_H
#include <stdint.h>

#include "pager_rec.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PL_NONE 0
#define PL_PACKET 1     /* a packet checked: line says so */
#define PL_READY 2      /* a message to play: pager_listen_take */

/* ring: n halfwords (a power of 2) in DMA-capable RAM; max: the most frames a message may have.
   Takes 14.5 KB of the encoder's arena until pager_listen_stop: never while the encoder runs.
   0, or -1 (the arena is taken). */
int pager_listen_start(const pr_host *h, int16_t *ring, unsigned n, unsigned max);
/* Takes in what the ring holds. now: ms. line (len bytes) gets a report when it returns
   PL_PACKET or PL_READY. */
int pager_listen_poll(uint32_t now, char *line, unsigned len);
/* After PL_READY: the message's frames (missing ones filled from their neighbours) into out
   (max frames x 6 bytes). Returns the frames. */
unsigned pager_listen_take(uint8_t *out, unsigned max);
void pager_listen_stop(void);
/* 1 while something is coming in: a packet after a strong sync (RR_SYNC_STRONG, so noise seldom
   lights it), or a message part way there; 0 otherwise (Jeff 11-14: the light turns green while
   the modem is receiving). */
int pager_listen_busy(void);

#ifdef __cplusplus
}
#endif

#endif
