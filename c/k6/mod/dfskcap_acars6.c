/* dfskcap_acars6: dfskcap_acars.c on 6 m, 50.650 MHz, for the bench: the IC-7100 has AM on HF
   and 50 MHz only, so this is where the plain FSK receiver can be shown a strong AM signal
   (python3 -m modem.acars probe OUT.wav --only R --where 50.650, sent in AM). 60 s after its
   start (its own upload on 440.850 MHz ends, the sender changes band), then the eight narrow
   front ends of DFSK_NARROW for 22 s each, every capture's words said. Receive only.
   docs/k6-acars.md.
   SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_NARROW
#define DFSK_DUMP_ALL
#define PROBE_HZ 50650000u
#define WAIT_MS 60000u
#define FE_S 22
#include "dfskcap.c"
