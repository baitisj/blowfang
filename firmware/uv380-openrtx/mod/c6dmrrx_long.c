/* c6dmrrx listening for 60 s with soft values of the first 64 synced bursts: room for five
   IC-7100 sends in one run (each ~4 s keyed plus the gaps), for comparing levels and equalisers.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define RX_MS 60000u
#define RX_SOFT 64u
#include "c6dmrrx.c"
