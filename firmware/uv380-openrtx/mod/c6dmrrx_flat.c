/* c6dmrrx with the AT1846S left in OpenRTX's FM set-up (filters, de-emphasis): does the C6000
   lock on DMR through the FM audio path at all? The comparison run for c6dmrrx.c.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define RX_AT 0
#include "c6dmrrx.c"
