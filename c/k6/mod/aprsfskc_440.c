/* aprsfskc_440: aprsfskb_440.c with the search started again every 59 ms while nothing comes, as
   aprsfsk does it by accident: is that what ends a packet the chip is still in? (the third bench
   run: after one turning off and on the chip ran on to its 256 bytes). SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_STREAM
#define FSK_AGAIN 59u
#define APRS_HZ 440850000u
#define RUN_MS 110000u
#define APRS_FSK_DUMP 2
#include "aprsclk.c"
