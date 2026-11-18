/* c6ysftx ON AIR, the data plan: the Morse ident AG7EW, then the UV-380's channel (441.850 or
   440.850 MHz; api->tx_tone refuses any other) at the channel's power (the bench: power 0), the
   YSF transmission of ysftx_data.h through the C6000's physical layer mode. Run c6ysftx.c (dry)
   first; the bench runner's uv380.ysf step sends it. SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the ident first and the transmit guard */
#define TX_DRY 0
#define YSF_PLAN_DATA
#include "c6ysftx.c"
