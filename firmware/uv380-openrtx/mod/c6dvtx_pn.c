/* c6dvtx ON AIR with the raw test pattern (dv_pn(k) in burst k, no FEC), step V1: the host learns
   from it how page 3's bits land on the air (modem/dmrvoice.py learn_map). The Morse ident AG7EW,
   then the DMR voice call at the radio's power on its channel (api->tx_tone keys only 441.850 or
   440.850 MHz). Only with Jeff at the radio, or at 0 W; run c6dvtx.c (dry) first.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the signer reads only this file: the ident first and the
                                       transmit guard (11-16); an older image refuses it */
#define TX_DRY 0
#define TX_SRC 2
#include "c6dvtx.c"
