/* rangerx_b: rangerx for the bench (docs/k6-range.md, /mnt/project-files/k6-range/bench-keys.md):
   on the radio's own frequency (440.850), 32 s after its start (it starts about 29 s before its
   upload's audio ends), then the ladder: nine settings of 45 s, the front end's path and the
   receiver's gain stepped down to bring the IC-7100's signal toward the edge (s and v lines).
   Each setting's first capture is said too (c and d lines, python3 -m modem.k6r dump LOG): the
   chip's samples at each level, for the decoder's sake. Receive only.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define RG_LADDER
#define RG_DUMP 900
#define DUMP_CAPS 1u
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#include "rangerx.c"
