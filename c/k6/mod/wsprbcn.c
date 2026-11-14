/* wsprbcn: wsprtx as a beacon: six WSPR frames, one every 10 minutes (an hour), each with its
   MCW ID, on UTC's 2-minute grid from a sync mark (`tools/k6_wspr.py sync`: the IC-7100's
   carrier dropped 10 s before a slot; without one in 150 s it starts at once); a held key stops
   it. SPDX-License-Identifier: MIT */
#define WSPR_REPEAT 6
#define WSPR_EVERY 5
#define WSPR_SYNC_MS 10000
#define K6_MODULE_LEVEL 3   /* wsprtx.c's, here too so the tools read it from this file */
#define K6_NAME "WSPR BEACON"   /* the module list's name, not wsprtx's */
#include "wsprtx.c"
