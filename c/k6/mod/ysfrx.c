/* ysfrx: receive sotto sent as YSF (C4FM) voice frames on one frequency (modem/ysf.py,
   docs/uv380-ysf.md): dmrdet's 4800-baud 4FSK IF front end (the GP0 tap, 2 grid points a
   symbol, the 13-tap matched filter) with the YSF frame receiver of c/k6/mod/ysfdec.inc and
   the decoders of c/k6/ysfdec.h (FICH: K=5 code, Golay, CRC-16; sotto VW payload: PN9,
   K=7 rate 7/18 tail-biting Viterbi, a CRC-8 per 6-byte sotto 1200 superframe). Receive
   only, no playback yet (the firmware has no sotto call for modules). Lines, besides
   dmrdet's, once a call is over (none during one: a line's ~5 ms pause costs superframes):
     Y call fr=10 fich=10 vw=40/40 crc=d821    a call: frames, FICHs that passed their CRC,
                                               superframes that passed theirs of those read,
                                               CRC-16 of all the superframes
     Y d 0 000102...                           the superframes kept (50), 24 bytes a line
   6.7 KB of code. Scratch past it: the filter's 256-byte ring, the receiver's 4.0 KB state and
   the 512-byte DMA ring (~200 B to spare in the 11,776-byte block), or it doesn't run (-6).
   docs/k6-c4fm.md, "Receiving C4FM sotto".
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT s         /* -Os: the decoders' steps must stay well under the DMA ring's
                                   93 us (DS_NOSYNC gave back the room) */
#ifndef FREQ_HZ
#define FREQ_HZ 440850000u      /* the bench link channel: the Pluto's sends, with its MCW ID */
#endif
#define YSF_DECODE 1
#ifndef RSZ
#define RSZ 512u
#endif
#include "dmrdet.c"
#ifndef K6_NAME
#define K6_NAME "YSF RX"
#endif
K6_MODULE_NAME(K6_NAME);
