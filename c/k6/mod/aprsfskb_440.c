/* aprsfskb_440: aprsfskb.c on the bench channel, 440.850 MHz, for 5 minutes, every packet's words said. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_SEEK 0x33E3u
#define APRS_HZ 440850000u
#define RUN_MS 300000u
#define APRS_FSK_DUMP 2
#include "aprsclk.c"
