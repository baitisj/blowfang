/* aprsfskd_440: aprsfskb_440.c with the receiver's de-emphasis off (REG_2B + 0100): which way do
   the modem's bits lean on a pre-emphasised packet? SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_STREAM
#define FSK_AF 0x0100u
#define APRS_HZ 440850000u
#define RUN_MS 110000u
#define APRS_FSK_DUMP 2
#include "aprsclk.c"
