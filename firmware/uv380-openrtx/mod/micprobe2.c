/* micprobe2: micprobe's second round, on the two register 26 values where the mic showed
   (26=7d and bd, 11-13 19:45Z): the same with the mic preamp off, their neighbours, the codec
   sets OpenGD77 and OpenRTX use for the mic, and the codec bit sweeps on 26=7d.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#define MP_ROUND2 1
#define MP_BASE 0x7Du
#include "micprobe.c"
