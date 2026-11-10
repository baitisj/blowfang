/* dfskcap_acars: dfskcap.c on a real ACARS channel, 131.550 MHz, four front ends of 150 s (FM
   as tuned, the AF source set to AM, FM tuned to one side in the narrow filter, AM narrow).
   Every capture gets a c line with its RSSI, those that go on like a pre-key their words,
   and every carrier (a rise of the RSSI) a b line: is there traffic at all?
   Receive only: nothing is ever sent on airband. docs/k6-acars.md. SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#include "dfskcap.c"
