/* aprslad_g: aprsdfsk_440 (440.850 MHz) for the APRS sensitivity ladder, setting "gain"
   (docs/k6-aprs-clock.md "Pluto ladder"); 400 s: the upload's ID, two minutes for the
   receiver to settle after its tune (insight 48), and a pluto_bench steps ladder. Its y lines
   carry r=, the packet's strongest RSSI. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_PLAIN
#define APRS_HZ 440850000u
#ifndef RUN_MS
#define RUN_MS 400000u
#endif
#define DF_SET "gain"
#define DF_GAIN 0x03FFu
#include "aprsclk.c"
