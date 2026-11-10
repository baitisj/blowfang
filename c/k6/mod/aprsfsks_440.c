/* aprsfsks_440: aprsfsk_440.c going round eight settings of the modem (REG_58, REG_2B), one every
   9.641 s, a round of the bench's kinds of packet (python3 -m modem.aprs wav OUT.wav --now --kinds
   --repeat 16): which setting reads which kind? 210 s. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_SWEEP 9641u
#define APRS_HZ 440850000u
#define RUN_MS 210000u
#include "aprsclk.c"
