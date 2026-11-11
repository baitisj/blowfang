/* samedf: the FD6818's plain FSK receiver as a sampler of NOAA SAME's audio. A probe.

   samecap (bench 2026-11-11 08:31Z) showed the plain receiver at SAME's own baud never starts on
   SAME's tones. As a sampler it might do what aprsdfsk does for Bell 202: the receiver slices the
   FM discriminator's sign on a clock that follows the transitions, so a tone near half its rate
   comes as 1010... (2200 Hz at a nominal 4800 came at 4400, docs/k6-aprs-clock.md). At 4166.7
   samples a second (8 x 520.83) SAME's 2083.3 Hz mark is two samples a cycle and its 1562.5 Hz
   space 2.67: a bit is eight samples, four cycles of mark or three of space. The preamble (AB, low
   bit first) has a run of three mark bits every byte, 24 samples of 1010...: the sync word AAAA.
   At 4800 or 6347 (REG_72 at its top) a second the tones are 2.3 and 3.1 or 3.0 and 4.1 samples a
   cycle, which a fixed clock samples well at any phase: the host's model finds the words 952A and
   6666 in every preamble there. This module records the samples for the host (python3 -m
   modem.same df LOG). pocdump.c with these settings, FE_S each after WAIT_MS, the AF path as found:
      0  4166.7 a second  sync aaaa   the mark at two samples a cycle, if the chip's clock locks on
      1  4800             952a        a fixed clock's preamble
      2  6347             6666        a fixed clock's preamble
      3  4800             aaaa        if the chip's clock comes down to the mark
   From the chip's first word DUMPW words are said. Receive only.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define DUMP_NAME "samedf"
#define DUMP_NV 4
/* REG_58, REG_72, a word in 1/16 ticks, sync, REG_2B bits, 0: no preamble to skip */
#define DUMP_V                               \
  {0x00E9, 43018, 614, 0xAAAA, 0, 0},        \
      {0x00E9, 49557, 533, 0x952A, 0, 0},    \
      {0x00E9, 65535, 403, 0x6666, 0, 0},    \
      {0x00E9, 49557, 533, 0xAAAA, 0, 0}
#define DUMPW 256u      /* 4096 samples: 1 s at 4166.7 a second, 64 bytes of SAME */
#include "pocdump.c"
