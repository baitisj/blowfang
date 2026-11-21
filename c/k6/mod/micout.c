/* micout: mic64 with the programming cable pulled out of the K6's jack (2026-11-20, Jeff: "WE DID
   EVERY TEST WITH THE SERIAL JACK IN THE K6"; the cable takes the mic out, as the UV-380's does).
   The screen walks the bench through it: PULL CABLE / THEN ANY KEY, QUIET PLEASE while it waits
   for the sound and records, TALK NOW for the last TALK_N samples, PLUG CABLE IN / THEN ANY KEY,
   and only then the lines, mic64's own plus "mic64 out talk=N". Keyed on the bench frequency with
   the PA off, as mic64 (the mic never on the air). Level 13. docs/k6-mic-probe.md.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 13
#define MIC64_OUT 1
#ifndef WAIT_MS
#define WAIT_MS 300000u /* 5 minutes for the sound after the first key */
#endif
#include "mic64.c"
