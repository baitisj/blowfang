/* aprsfsk: aprsclk.c behind the FD6818's own FSK modem instead of the IF tap: APRS on 144.390 MHz
   for 15 minutes, the radio's clock set from its timestamps. The chip's 1200/2400 Hz receiver
   with REG_58 = 0x33A3 slices Bell 202 (docs/k6-aprs-clock.md, probe 4); the sync word is two
   flags. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#include "aprsclk.c"
