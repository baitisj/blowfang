/* ysfrx: receive sotto sent as YSF (C4FM) voice frames on one frequency (modem/ysf.py,
   docs/uv380-ysf.md): dmrdet's 4800-baud 4FSK IF front end (the GP0 tap, 2 grid points a
   symbol, the 13-tap matched filter) with the YSF frame receiver of c/k6/mod/ysfdec.inc and
   the decoders of c/k6/ysfdec.h (FICH: K=5 code, Golay, CRC-16; sotto VW payload: PN9,
   K=7 rate 7/18 tail-biting Viterbi, a CRC-8 per 6-byte sotto 1200 superframe). Receive
   only, no playback yet (the firmware has no sotto call for modules). Lines, besides
   dmrdet's, once a call is over (none during one: a line's ~5 ms pause costs superframes):
     Y call fr=41 fich=40 vw=190/200 sf=200 miss=10 crc=9d79
                                 a call: frames, FICHs good, superframes good / read, the
                                 message's superframes and those filled in, its CRC-16
     Y d 0 000102...             the message, 24 bytes a line
     Y l 0 0 0 0 0 0 0 0 f=0     after each "s" line: ring overruns by the kind of call that
     Y m 52 56 72 64 48 32 24 4  ran last (yx_kind), and each kind's longest call, us
   7.7 KB of code. Scratch past it: the filter's 256-byte ring, the receiver's 2.3 KB state,
   the DMA ring, then the message (at least 100 superframes), or it doesn't run (-6).
   docs/k6-c4fm.md, "Receiving C4FM sotto".
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT s         /* -Os: the decoders' steps must stay well under the DMA ring */
#ifndef FREQ_HZ
#define FREQ_HZ 440850000u      /* the bench link channel: the Pluto's sends, with its MCW ID */
#endif
#define YSF_DECODE 1
#ifndef RSZ
#define RSZ 1024u               /* the DMA ring: 186 us. Emulated, the longest calls take 50-72 us,
                                   near the 512-byte ring's 81 us, and on the radio (its own
                                   interrupts besides) run 2 lost about a ring a frame */
#endif
#include "dmrdet.c"
#ifndef K6_NAME
#define K6_NAME "YSF RX"
#endif
K6_MODULE_NAME(K6_NAME);
