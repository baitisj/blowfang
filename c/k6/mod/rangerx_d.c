/* rangerx_d: rangerx for the bench with each capture's words said (bench run 2: they showed
   that the chip's sampler slips, docs/k6-range.md). The radio's own frequency (440.850), 32 s
   after its start, then 120 s; the first six captures said after each one ends (c and d lines;
   python3 -m modem.k6r dump LOG). 900 words: a capture's lead and a whole packet. Receive only.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define RG_DUMP 900
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#ifndef RG_S
#define RG_S 120u
#endif
#include "rangerx.c"
