/* c6dmrtx ON AIR with the radio's own message (the last recording or message heard, what PTT
   sends) instead of the test pattern: the Morse ident, then 1 W on 440.850 MHz. Only with Jeff
   at the radio.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the signer reads only this file: the ident first and the
                                       transmit guard (11-16); an older image refuses it */
#define TX_DRY 0
#define TX_SRC 1
#include "c6dmrtx.c"
