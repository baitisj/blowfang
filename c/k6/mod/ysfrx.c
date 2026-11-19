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
     Y t w=585735 585012          the grid's step as the call began and ended, then
     Y t 0 6e0000 70d9c0 6c01bf  the frames: 3 bytes each, sync rho^2/2 (Q8), its offset
                                 (quarter points), flags (80 FICH good, 40 a wide search,
                                 20 a VW decoded, its good superframes' bits below)
     Y l 0 0 0 0 0 0 0 0 f=0     after each "s" line: ring overruns by the kind of call that
     Y m 52 56 72 64 48 32 24 4  ran last (yx_kind), and each kind's longest call, us
   7.7 KB of code (8.1 KB with YX_TRACE). Scratch past it: the filter's 256-byte ring, the receiver's 2.3 KB state,
   the DMA ring, then the message (at least 100 superframes), or it doesn't run (-6).
   docs/k6-c4fm.md, "Receiving C4FM sotto".
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT s         /* -Os: the decoders' steps must stay well under the DMA ring */
#ifndef FREQ_HZ
#define FREQ_HZ 440850000u      /* the bench link channel: the Pluto's sends, with its MCW ID */
#endif
#define YSF_DECODE 1
#define YX_CSD 1                /* the caller's callsign from the header or terminator */
#ifndef YX_NO_PLAY
#define YX_PLAY 1               /* on an image with module API level 11: the call played */
#ifndef YX_DUMP
#define YX_DUMP 0               /* (no "Y d" lines: the core's "K6 PLAY put" line has the CRC) */
#endif
#endif
/* YX_TRACE 1: "Y t" lines after each call, its frames' syncs (the bench build: 0.4 KB of code,
   the message buffer 69 superframes) */
#ifndef RSZ
#define RSZ 1024u               /* the DMA ring: 186 us. Emulated, the longest calls take 50-72 us,
                                   near the 512-byte ring's 81 us, and on the radio (its own
                                   interrupts besides) run 2 lost about a ring a frame */
#endif
#include "dmrdet.c"
#ifndef K6_NAME
#define K6_NAME "FUSOTTO RX"   /* (Jeff 11-19: sotto over YSF is FuSotto) */
#endif
K6_MODULE_NAME(K6_NAME);
