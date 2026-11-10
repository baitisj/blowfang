/* surfkeys: surf.c for an unattended bench run of the keys Jeff asked for on 2026-11-10
   (docs/k6-surf.md): it presses SURF_SCRIPT's keys itself, one every SURF_SCRIPT_MS, and before
   each press says what the last one left ("k9 440.8500 -71 -127 g1 70cm 25k": the key, the
   middle, its settled dBm, the settled floor, the fixed gain index (0 auto), the band and its
   step), then leaves after the last. With a carrier on the start frequency the gain lines
   measure the fixed-gain steps on the radio. Keys still work. Receive only.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 6
#define K6_MODULE_OPT z
/* 9: the top gain, 7 x 7: down to -45 dB, 8: the AGC, BAND x 3: airband (AM), #: its next step,
   5: its centre, BAND x 3: back to 70 cm (its default step) */
#define SURF_SCRIPT "97777777" "8" "NNN" "#5" "NNN"
#define SURF_SCRIPT_MS 3000
#include "surf.c"
