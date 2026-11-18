/* c6dvtx ON AIR with the raw test pattern (dv_pn(k) in burst k, no FEC), step V1: the host learns
   from it how page 3's bits land on the air (modem/dmrvoice.py learn_map). The Morse ident AG7EW,
   then the DMR voice call at the radio's power on its channel (api->tx_tone keys only 441.850 or
   440.850 MHz). Only with Jeff at the radio, or at 0 W; run c6dvtx.c (dry) first. The payload
   goes through V_SPI and U_SPI page 3 by turns, a superframe each (TX_VIA 2), so one call shows
   which port the encoder takes.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the signer reads only this file: the ident first and the
                                       transmit guard (11-16); an older image refuses it */
#define TX_DRY 0
#define TX_SRC 2
#define TX_VIA 2                    /* V_SPI and U_SPI page 3 by turns (V1c, 11-17) */
#include "c6dvtx.c"
