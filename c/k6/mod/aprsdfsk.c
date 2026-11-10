/* aprsdfsk: the APRS clock behind the chip's plain FSK receiver as a sampler of the audio
   (aprsclk.c, FSK_PLAIN): reads pre-emphasised packets too, which the 1200/2400 Hz receiver
   (aprsfskb.c) never did. 144.390 MHz. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_PLAIN
#include "aprsclk.c"
