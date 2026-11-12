/* rangerx_d: rangerx for the bench with each capture's words said (rangerx run 1 found every
   packet's sync but about 700 of 2064 coded bits wrong at every level: the words show what the
   chip's sampler hands over). The radio's own frequency (440.850), 32 s after its start, then
   120 s: 60 s with the AF path as found (s 0), 60 s with its de-emphasis off (s 1, REG_2B bit
   8); the first six captures of each said after them (c and d lines; python3 -m modem.k6r dump
   LOG). Receive only. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define RG_DUMP 720
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#ifndef RG_S
#define RG_S 120u
#endif
#include "rangerx.c"
