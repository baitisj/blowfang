/* aprsfskb: aprsfsk.c listening for its sync word with REG_58 = 0x33E3 (probe 4 saw that receiver start
   on a plain packet's flags) and writing 0x33A3 at the sync interrupt, in case 0x33A3 does not
   start by itself. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_SEEK 0x33E3u
#include "aprsclk.c"
