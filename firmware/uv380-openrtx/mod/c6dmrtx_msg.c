/* c6dmrtx ON AIR with the radio's own message (the last recording or message heard, what PTT
   sends) instead of the test pattern: 1 W on 440.850 MHz, then the Morse ident. Only with Jeff
   at the radio.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define TX_DRY 0
#define TX_SRC 1
#include "c6dmrtx.c"
