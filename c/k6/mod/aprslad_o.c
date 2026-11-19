/* aprslad_o: aprsdfsk_440 (440.850 MHz) for the APRS ladder, setting "offt" (wide, AFC on, AGC):
   each packet's opening flags say how far off the sender is (q=, ones less zeros: the chip
   slices at the tuned centre), and a packet with |q| >= DF_QMIN is given up, the K6 tuned
   DF_QHZ Hz a unit of q off and the packet read again from the flags still to come; tuned back
   after it (docs/k6-aprs-clock.md "Pluto ladder"). 400 s, as the other aprslad_* modules.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_PLAIN
#define APRS_HZ 440850000u
#ifndef RUN_MS
#define RUN_MS 400000u
#endif
#define DF_SET "offt"
#define DF_OFFT 1
#include "aprsclk.c"
