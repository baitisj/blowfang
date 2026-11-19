/* dfskcap_de: dfskcap (the plain FSK receiver's words on Bell 202) with its first setting from
   75 s after the start, so that a Pluto send after the upload's own report and ID lands in the
   main setting: what do the words of a de-emphasised packet look like, which aprsdfsk stops
   84 words in, where its opening flags end (docs/k6-aprs-clock.md "Pluto ladder").
   SPDX-License-Identifier: MIT */
#define WAIT_MS 75000u
#include "dfskcap.c"
