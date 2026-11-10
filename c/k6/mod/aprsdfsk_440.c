/* aprsdfsk_440: aprsdfsk.c on the bench channel, 440.850 MHz, for 240 s (python3 -m modem.aprs
   wav OUT.wav --now --kinds --only 1,2,3,4,5,7,9,10 --repeat 20). SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_PLAIN
#define APRS_HZ 440850000u
#define RUN_MS 240000u
#include "aprsclk.c"
