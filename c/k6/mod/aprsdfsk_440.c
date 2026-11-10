/* aprsdfsk_440: aprsdfsk.c on the bench channel, 440.850 MHz, for 240 s, the FIFO read by its
   almost-full flag and by time in turns of five rounds of the bench's kinds of packet (python3
   -m modem.aprs wav OUT.wav --now --kinds --repeat 20). SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_PLAIN
#define DF_MODE 2
#define APRS_HZ 440850000u
#define RUN_MS 240000u
#include "aprsclk.c"
