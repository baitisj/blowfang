/* dfskcap_dmr: dfskcap.c's recorder on the local DMR repeater's output, 440.925 MHz, receive
   only (DFSK_DMR). The question: does the chip's plain FSK receiver find DMR's sync words by
   itself? Their symbols are all outer ones, so their signs are a fixed pattern, which is the
   sync word here; what the chip then gives is the sign of every symbol, and the next bursts'
   syncs must show in it every 144 bits. If it does, DMR is found at the FM receiver's own
   sensitivity and not at the IF tap's (docs/k6-dmr.md). Eight settings of 0.8 s, 110 times
   round (12 minutes), from 32 s after the start; a key ends it. python3 -m modem.dmrsign LOG.
   SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_NARROW
#define DFSK_DMR
#define DFSK_ROUNDS 110u
#define DFSK_DUMP_ALL
#define PROBE_HZ 440925000u
#define SLOT_SCALE 100u
#define FE_S 8
#include "dfskcap.c"
