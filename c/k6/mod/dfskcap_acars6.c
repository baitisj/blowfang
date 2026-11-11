/* dfskcap_acars6: dfskcap_acars.c on 6 m, 50.650 MHz, for the bench: the IC-7100 has AM on HF
   and 50 MHz only, so this is where the plain FSK receiver can be shown a strong AM signal
   (python3 -m modem.acars probe OUT.wav --only R --where 50.650, sent in AM). 60 s after its
   start (its own upload on 440.850 MHz ends, the sender changes band), then the eight front
   ends of DFSK_ZIF (the zero IF, the chip's double sideband mode) for 22 s each, every
   capture's words said. Build with DFSK_NARROW instead for the sixth run's front ends.
   Receive only. docs/k6-acars.md.
   SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_ZIF
#define DFSK_DUMP_ALL
#define PROBE_HZ 50650000u
#define WAIT_MS 60000u
#define FE_S 22
#include "dfskcap.c"
