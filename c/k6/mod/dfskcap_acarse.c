/* dfskcap_acarse: dfskcap_acarsd.c's recorder with the tone receiver's second round of
   settings (DFSK_TONE 2), in FM on 440.850 MHz for the bench. The first round showed the
   receiver giving its tone bits with the pre-key as the sync word, a lone bit of 1200 Hz lost
   at 2400 baud and kept as one sample at 4800. So: every setting with that sync word, at
   4800 baud and over (5400, 6000, 6348), with REG_58's bits 7:6, its RX bandwidth bits and
   two other RX modes at 4800 baud. The IC-7100 sends K, R and T units in turn (python3 -m
   modem.acars probe --only KRT --gap 0.4) in FM at power 0; sixteen settings of 11 s from
   60 s after the start. Receive only. docs/k6-acars.md. SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_TONE 2
#define DFSK_DUMP_ALL
#define PROBE_HZ 440850000u
#define WAIT_MS 60000u
#define FE_S 11
#include "dfskcap.c"
