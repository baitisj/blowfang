/* aprsdfsk_o: aprsdfsk (144.390 MHz, 15 min) with each packet tuned to its sender's offset from
   its opening flags (DF_OFFT, as aprslad_o: the Pluto ladder read 60 of 108 packets over
   +-1 kHz of offsets against 23 without, docs/k6-aprs-clock.md "Pluto ladder"). Its y lines
   carry r= (the packet's strongest RSSI), q= (the flags' ones less zeros) and o= (the offset
   read at); on the air they say where real senders sit. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_PLAIN
#define DF_SET "offt"
#define DF_OFFT 1
#include "aprsclk.c"
