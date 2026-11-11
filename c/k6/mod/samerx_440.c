/* samerx_440: samerx.c on the bench, 440.850 MHz: its three rates, FE_S seconds each after a
   32 s wait (the bench sends SAME-format bursts, python3 -m modem.same wav). Receive only.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define SAME_HZ 440850000u
#define SAME_BENCH
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#define FE_S 45u
#include "samerx.c"
