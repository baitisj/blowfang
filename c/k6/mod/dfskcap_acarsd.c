/* dfskcap_acarsd: dfskcap.c's ACARS recorder on the chip's 1200/2400 Hz tone receiver
   (DFSK_TONE), in FM on 440.850 MHz for the bench. ACARS is that receiver's own modulation at
   2400 baud, but acarsfsk got no sync from it on SYN SYN, and the pager none at 2400 baud. So
   here the pre-key is the sync word and the chip hands over the tone bits of all that
   follows; the host does the framing (python3 -m modem.acars tone LOG). The IC-7100 sends K,
   R and L units in turn (python3 -m modem.acars probe --only KRL --gap 0.4) in FM at power 0;
   sixteen settings of 11 s from 60 s after the start, as for dfskcap_acarsc. Receive only.
   docs/k6-acars.md. SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_TONE
#define DFSK_DUMP_ALL
#define PROBE_HZ 440850000u
#define WAIT_MS 60000u
#define FE_S 11
#include "dfskcap.c"
