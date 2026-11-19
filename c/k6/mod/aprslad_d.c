/* aprslad_d: aprsdfsk_440 (440.850 MHz) for the APRS sensitivity ladder, setting "nodeemph"
   (REG_2B + 0100: the receiver's de-emphasis off; docs/k6-aprs-clock.md "Pluto ladder");
   400 s: the upload's ID, two minutes for the receiver to settle after its tune (insight 48),
   and a pluto_bench steps ladder. Its y lines carry r=, the packet's strongest RSSI. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_PLAIN
#define APRS_HZ 440850000u
#ifndef RUN_MS
#define RUN_MS 400000u
#endif
#define DF_SET "nodeemph"
#define DF_AF 0x0100u
#include "aprsclk.c"
