/* c6m17tx ON AIR, the sms plan: the Morse ident AG7EW, then the UV-380's channel (441.850 or
   440.850 MHz; api->tx_tone refuses any other) at the channel's power (the bench: power 0), the
   M17 transmission of m17tx_sms.h through the C6000's physical layer mode. Run c6m17tx.c (dry)
   first; the bench runner's uv380.m17 step sends it. SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the ident first and the transmit guard */
#define TX_DRY 0
#define M17_PLAN_SMS
#include "c6m17tx.c"
