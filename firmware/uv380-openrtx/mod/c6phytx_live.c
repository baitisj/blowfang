/* c6phytx ON AIR: the Morse ident AG7EW, then 440.850 MHz for 4.6 s (api->tx_tone refuses any
   other frequency) at the channel's power (the bench: "power 0", "zero 2"), fsk24rx's test
   packets as plain 2FSK at 2400 baud through the C6000's physical layer mode, polarity
   alternating packet by packet (variants 20 and 21). Only with Jeff around; run c6phytx.c (dry)
   first. SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4           /* the ident first and the transmit guard: older images refuse it */
#define TX_DRY 0
#include "c6phytx.c"
