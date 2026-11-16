/* digi_tx.h: FT8 / WSPR frames from the RT-950 Pro by PLL steps (radios/rt950/src/digi_tx.c).
   SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef DIGI_TX_H
#define DIGI_TX_H

#include <stdint.h>
#include "rt_api.h"
#include "../../../c/common/tone_fsk.h"

#define DIGI_ID "AG7EW"         /* every test transmission identifies (Jeff, 2026-11-07) */
#define DIGI_LO 430000000u      /* 70 cm as Lynden has it (north of Line A): the bench band */
#define DIGI_HI 450000000u

#define DIGI_NOTX -1            /* the radio has no transmitter in its API yet */
#define DIGI_BAND -2            /* outside 70 cm without outside_ok */
#define DIGI_REFUSED -3         /* tx_on refused */
#define DIGI_KEY -4             /* a key was pressed: keyed down at once */
#define DIGI_CFG -5             /* tf_init refused the configuration */

/* Sends one frame of nsym tones on dial_hz (USB: tone 0 at dial + c->base_hz) by stepping chip
   0's PLL c->writes times a second (c/common/tone_fsk.c picks each write), then DIGI_ID in CW at
   20 wpm on tone 0's frequency by keying the carrier. Chip 1 is off for the whole transmission.
   outside_ok: 1 only for a run Jeff has OK'd outside 70 cm (2 m FT8 on 144.174, say); every
   other call stays in 430-450 MHz. Returns the PLL writes sent, or a DIGI_* error. */
int digi_send(const rt_api_t *api, uint32_t dial_hz, const tf_cfg *c, const uint8_t *tones, unsigned nsym,
              int power, int outside_ok);

#endif
