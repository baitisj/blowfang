/* dfskcap_dmr3: dfskcap_dmr's third table (DFSK_DMR3), half an hour on 440.925 MHz, receive
   only. Runs J and K: the chip's plain FSK receiver finds DMR's sync at -81 to -87 dBm in
   every setting. How weak may the signal be? The repeater has one level here, so the receiver
   is made deaf by steps: its gain fixed (DFSK_GAIN: REG_13, REG_7E bit 15) at about 0, -14,
   -24, -35, -40, -43, -51 and -60 dB, taken off the front first, so that the signal sinks
   towards the noise of the stages behind. Each slot's p= and f= give the signal and the floor
   at that gain; the starts say from how many dB over the floor the chip finds the sync. One
   setting (9B4D, the narrow filter, the AFC off), eight gains of 0.8 s, 280 times round; a
   capture is cut at 96 words (a third of a second) so that a key-up of 7 s meets every gain;
   from 32 s after the start; a key ends it. python3 -m modem.dmrsign LOG. docs/k6-dmr.md.
   SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_NARROW
#define DFSK_DMR
#define DFSK_DMR3
#define DFSK_GAIN
#define DFSK_CAPMAX 96u
#define DFSK_ROUNDS 280u
#define DFSK_DUMP_ALL
#define PROBE_HZ 440925000u
#define SLOT_SCALE 100u
#define FE_S 8
#include "dfskcap.c"
