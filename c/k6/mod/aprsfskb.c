/* aprsfskb: aprsfsk.c with a sync word of two flags (REG_59 = 00F0): it starts on a shorter opening,
   and on noise several times a second. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_CTRL 0x00F0u
#include "aprsclk.c"
