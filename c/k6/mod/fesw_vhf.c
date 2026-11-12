/* fesw_vhf: fesw up and down the VHF path, three minutes, receive only. Run 1
   (docs/k6-fesw.md) left the 2 m question half answered: the -111 dBm floor on 144.390 MHz
   goes with the VHF path (the antenna, the cable or the K6's own VHF amplifier), not with
   the chip. This table asks which, as far as a run with no hands can:
     - the floor alike from 136 to 174 MHz and at every hour: the amplifier's own noise (or a
       noise so broad it is as good as that), and no antenna will mend it;
     - a hump round 144.390, or a floor that moves with the hour: it comes in from outside,
       and the antenna-off check (Jeff's hands) is worth its while.
   The steps close to 144.390 (50 kHz either side) tell a narrow source from a broad one.
   162.550 (NOAA's carrier) and 440.925 (the UHF path's floor, -132 in run 1) are the
   references. Each frequency in the three states of the switch as fesw has them, so the
   path's share of the floor is read at every one. Run it at several hours and lay the
   reports side by side: python3 -m modem.fesw LOG. SPDX-License-Identifier: MIT */
#define FE_KHZ   \
  136000u,       \
  140000u,       \
  143000u,       \
  144200u,       \
  144340u,       \
  144390u,       \
  144440u,       \
  144600u,       \
  146000u,       \
  148000u,       \
  152000u,       \
  156000u,       \
  160000u,       \
  162550u,       \
  170000u,       \
  440925u
#ifndef FE_ROUNDS
#define FE_ROUNDS 5u
#endif
#include "fesw.c"
