/* c6dmrtx ON AIR: the Morse ident AG7EW, then 1 W on 440.850 MHz for 4.6 s (api->tx_tone
   refuses any other frequency), the test pattern as DMR rate 1/2 data bursts. Only with Jeff
   at the radio (project rule: no UV-380 TX while he is away); run c6dmrtx.c (dry) first.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the signer reads only this file: the ident first and the
                                       transmit guard (11-16); an older image refuses it */
#define TX_DRY 0
#include "c6dmrtx.c"
