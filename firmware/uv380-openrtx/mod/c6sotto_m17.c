/* c6sotto ON AIR, M17: the radio's sotto message through the shared
   HR_C6000 core (c6sotto.c, docs/uv380-multimode.md). The Morse ident AG7EW, then the UV-380's
   channel (441.850 or 440.850 MHz; api->tx_tone refuses any other) at the channel's power (the
   bench: power 0). Run c6sotto_m17_dry.c first.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the ident first and the transmit guard */
#define SOTTO_AIR 1
#define TX_DRY 0
#include "c6sotto.c"
