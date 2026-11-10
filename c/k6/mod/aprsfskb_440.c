/* aprsfskb_440: aprsfskb.c on the bench channel, 440.850 MHz, for 110 s, every packet's words said. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_STREAM
#define APRS_HZ 440850000u
#define RUN_MS 110000u
#define APRS_FSK_DUMP 2
#include "aprsclk.c"
