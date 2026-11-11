/* dfskcap_dmr2: dfskcap_dmr's second table (DFSK_DMR2), an hour on 440.925 MHz, receive only.
   Run J showed that the chip's plain FSK receiver finds DMR's sync by its signs, and that its
   clock drops sign 16 of the sync field more often than not; so the sync word is 9B4D or
   CD4D (the last sixteen signs without the dropped one), in the wide and the narrow filter,
   with the AFC on or off, with REG_58's bits 7:6 = 10 and with the AF filters as found: which
   starts most often on a key-up, and from what level? Eight settings of 0.8 s, 560 times
   round (an hour: the repeater keys every few minutes), from 32 s after the start; a key
   ends it. python3 -m modem.dmrsign LOG. docs/k6-dmr.md. SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_NARROW
#define DFSK_DMR
#define DFSK_DMR2
#define DFSK_ROUNDS 560u
#define DFSK_DUMP_ALL
#define PROBE_HZ 440925000u
#define SLOT_SCALE 100u
#define FE_S 8
#include "dfskcap.c"
