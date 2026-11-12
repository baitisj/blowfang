/* rangerx_b: rangerx for the bench (docs/k6-range.md, /mnt/project-files/k6-range/bench-keys.md):
   on the radio's own frequency (440.850), 32 s after its start (it starts about 29 s before its
   upload's audio ends), then the ladder: nine settings of 45 s, the front end's path and the
   receiver's gain stepped down to bring the IC-7100's signal toward the edge (s and v lines).
   Receive only. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define RG_LADDER
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#include "rangerx.c"
