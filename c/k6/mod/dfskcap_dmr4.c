/* dfskcap_dmr4: dfskcap_dmr3's gain steps in blocks of three kinds (DFSK_BLOCK), some two
   hours and twenty minutes on 440.925 MHz, receive only. Run L put the edge of the chip's DMR
   sync at 10 dB over the floor, but it tuned at each of its 0.8 s settings, and the tuning
   restarts the receiver: after one, the POCSAG sampler read noisy bits for a minute or two
   (docs/k6-pocsag.md, runs 6 to 8). So eight gains again (run L's without two of the strong
   steps, and with the mixer's two steps between 001F and 0007, where run L had none between
   24 and 10 dB over the floor), 144 times round a block (922 s or a little more, so that
   every block meets a key-up of the repeater's, which come 903 s apart), the blocks by turns:
      bk=0   the tuning at every setting, the AFC off: as run L
      bk=1   no tuning, the AFC off
      bk=2   the AFC on (one tuning at the block's start for it to take hold), then none
   three times each. If bk=1 reads further down than bk=0, the restart cost run L and a
   detector should not tune more often than it must; bk=2 against bk=1 says what the AFC does
   to a burst it has no time to pull in. From 32 s after the start; a key ends it.
   python3 -m modem.dmrsign LOG. docs/k6-dmr.md. SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_NARROW
#define DFSK_DMR
#define DFSK_DMR3
#define DFSK_GAIN
#define DFSK_BLOCK 144u
#define DFSK_CAPMAX 96u
#define DFSK_ROUNDS 1296u
#define DFSK_DUMP_ALL
#define PROBE_HZ 440925000u
#define SLOT_SCALE 100u
#define FE_S 8
#include "dfskcap.c"
