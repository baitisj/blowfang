/* dfskcap_acars6am: dfskcap_acars6.c's recorder on 50.650 MHz with the chip in AM at its
   normal IF (DFSK_TONE 4). The tone receiver gave its tone bits in FM and at the zero IF, and
   lost half cycles of 1200 Hz in both; it was never shown an AM signal with the chip in its
   AM mode: the AF source 7, the AFC off, the AM mode's AGC, the IF as found. Nothing handed
   over: the AM audio does not reach the tone receiver. Bits: how many are lost against the
   zero IF's (the last setting is that one again), and the normal IF's floor is some 28 dB
   under the zero IF's. The IC-7100 sends K, R and T units in turn in AM (python3 -m
   modem.acars probe --only KRT --gap 0.4 --levels 1,1.8 --where 50.650); sixteen settings
   of 11 s from 60 s after the start. DFSK_ZIF is for the IF and the AFC a setting. Receive
   only. docs/k6-acars.md. SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_ZIF
#define DFSK_TONE 4
#define DFSK_DUMP_ALL
#define PROBE_HZ 50650000u
#define WAIT_MS 60000u
#define FE_S 11
#include "dfskcap.c"
