/* aprsfsk_440: aprsfsk.c on the bench channel, 440.850 MHz, for 5 minutes, every packet's words said:
   the on-air loopback (python -m modem.aprs wav OUT.wav --now --repeat N --swap, then
   tools/k6_send.sh OUT.wav). SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define APRS_HZ 440850000u
#define RUN_MS 300000u
#define APRS_FSK_DUMP 2
#include "aprsclk.c"
