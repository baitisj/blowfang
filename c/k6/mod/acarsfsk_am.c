/* acarsfsk_am: acarsfsk.c's AM half. The IC-7100 sends the probe's signal in AM on 440.850 MHz
   and the K6 tries six front ends (the AF source set to AM; FM tuned to one side in the narrow
   filter) with three modem settings and two sync words each, 5 s a setting, once round.
   Receive only. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define ACARS_AM
#include "acarsfsk.c"
