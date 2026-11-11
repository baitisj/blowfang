/* pocsag_440: pocsag.c on the bench channel, 440.850 MHz, three settings of 60 s after 32 s (the
   module starts about 29 s before its upload's audio ends): python3 -m modem.pocsag wav OUT.wav
   --rounds 18, sent right after the upload; python3 -m modem.pocsag report LOG.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define POC_HZ 440850000u
#define POC_BENCH
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#define FE_S 60u
#include "pocsag.c"
