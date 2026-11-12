/* dfskcap_dmr5: dfskcap_dmr tuned off the repeater by steps (DFSK_DMR5), an hour and a half
   on 440.925 MHz, receive only. Run M: with the AFC on more of DMR's sync fields come
   whole, so where the signal sits against the chip's slicer matters. Where does the slicer sit: at the tuned
   centre, or at the signal's own mean? One setting (9B4D, the narrow filter, the AFC off, the
   gain the radio's own) tuned 1.3, 0.9, 0.5 and 0.2 kHz below and above, 0.8 s each, 840
   times round. DMR's inner symbols are 648 Hz off the middle and its outer ones 1944 Hz: a
   slicer at the tuned centre puts the inner symbols of one sign on the wrong side from about
   0.65 kHz off, and the share of ones in the captures then goes from a half to a quarter one
   way and three quarters the other, while the sync fields (outer symbols only) hold. Then
   listens off tune either way tell the outer symbols from the inner ones (a lead towards the
   colour code for the explorer), and a two-level mode through this receiver must be tuned
   within a few hundred Hz. If the share stays a half, the chip takes the mean out itself.
   The whole fields' share by offset also says how far the K6 sits from the repeater. From
   32 s after the start; a key ends it. python3 -m modem.dmrsign LOG. docs/k6-dmr.md.
   SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_NARROW
#define DFSK_DMR
#define DFSK_DMR5
#define DFSK_CAPMAX 96u
#define DFSK_ROUNDS 840u
#define DFSK_DUMP_ALL
#define PROBE_HZ 440925000u
#define SLOT_SCALE 100u
#define FE_S 8
#include "dfskcap.c"
