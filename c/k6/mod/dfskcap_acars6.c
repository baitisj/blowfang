/* dfskcap_acars6: dfskcap_acars.c on 6 m, 50.650 MHz, for the bench: the IC-7100 has AM on HF
   and 50 MHz only, so this is where the plain FSK receiver can be shown a strong AM signal
   (python3 -m modem.acars probe OUT.wav --only R --where 50.650, sent in AM). 60 s after its
   start (its own upload on 440.850 MHz ends, the sender changes band), then, at the zero IF
   (DFSK_ZIF, the chip's double sideband mode, where the receiver takes in an AM block),
   sixteen settings of 11 s each of the chip's 1200/2400 Hz tone receiver (DFSK_TONE 3: the
   pick of dfskcap_acarsd's and dfskcap_acarse's, most with the pre-key as the sync word and
   the tone bits of all that follows said; the signal is then --only KRT --gap 0.4
   --levels 1,1.8). With DFSK_TONE 1: dfskcap_acarsd's own settings (staged for a run, not
   made). With DFSK_SETS in DFSK_TONE's place: dfskcap_acarsc's
   settings of the plain receiver (staged for a ninth run, not made); with DFSK_SWEEP and
   FE_S 22: the eighth run's clocks from 5200 to 6300 baud; with none of them: the seventh
   run's front ends (which IF, which AF source); with DFSK_NARROW in DFSK_ZIF's place: the
   sixth run's.
   Receive only. docs/k6-acars.md.
   SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_ZIF
#define DFSK_TONE 3
#define DFSK_DUMP_ALL
#define PROBE_HZ 50650000u
#define WAIT_MS 60000u
#define FE_S 11
#include "dfskcap.c"
