/* dfskcap_acarsb: dfskcap.c's ACARS recorder with the receiver's clock stepped from 5200 to
   6300 baud (DFSK_SWEEP), in FM on 440.850 MHz for the bench: at which baud does the chip give
   two samples for an interval of 1.5 (a change of tone) and still one for 1 and two for 2?
   The IC-7100 sends real blocks (python3 -m modem.acars probe --only R) in FM at power 0;
   eight front ends of 22 s from 32 s after the start. Receive only. docs/k6-acars.md.
   SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_SWEEP
#define DFSK_DUMP_ALL
#define PROBE_HZ 440850000u
#define FE_S 22
#include "dfskcap.c"
