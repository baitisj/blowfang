/* pocdump_1200: pocsag.c on the bench channel, 440.850 MHz, with every word of the first six
   1200 baud transmissions said after them (c and d lines, as pocdump says them): one setting of
   160 s after 32 s, for whole sampled transmissions, the one way up and the other (bench run 3:
   upside down decoded worse). python3 -m modem.pocsag wav OUT.wav --bauds 1200 --gap 2
   --rounds 40, sent right after the upload; python3 -m modem.pocsag dump LOG and report LOG.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define POC_HZ 440850000u
#define POC_DUMP 1100
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#ifndef FE_S
#define FE_S 160u
#endif
#include "pocsag.c"
