/* c6phytx ON AIR at 1200 baud (four symbols a bit, variants 22 and 23), otherwise as
   c6phytx_live.c: the ident, 440.850 MHz only, 4.6 s at the channel's power. Only with Jeff
   around. SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define UV_MODULE_LEVEL 4
#define TX_DRY 0
#define TX_BAUD 1200u
#define TX_SEQ 100u
#include "c6phytx.c"
