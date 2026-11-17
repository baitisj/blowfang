/* fsk24rx_sweep: the K6's plain FSK receive sensitivity at 2400, 1800 and 1200 baud, fsk24rx.c
   run as slots of 12 s on a fixed grid: D/2400, D/1800 (2.4K and 1.2K RX bandwidth) and D/1200
   in turn (REG_2B +0400), 32 slots (sweep 3, Jeff 11-16; sweep 1 had F/1800 and F/1200 here,
   which timed reads can't read). The ADALM-Pluto sends each slot its rate's test packets, one
   level step lower every four slots, 0 to -21 dB in 3 dB steps, timed from the "fsk24 start" line
   (python3 tools/pluto_bench.py fsk24 --sweep --follow LOG). Report: python3 -m modem.fsk24
   report --sweep LOG. The table below is python3 -m modem.fsk24 sweep-table (a test checks it).
   SPDX-License-Identifier: MIT */
#define FE_S 12u
#define F24_ABS 1
#define WAIT_MS 30000u
#define DUMPN 1u
#define F24_NV 32
#define F24_V \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 18584, 1422, 0x0400}, \
  {0x00E1, 18584, 1422, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 18584, 1422, 0x0400}, \
  {0x00E1, 18584, 1422, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 18584, 1422, 0x0400}, \
  {0x00E1, 18584, 1422, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 18584, 1422, 0x0400}, \
  {0x00E1, 18584, 1422, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 18584, 1422, 0x0400}, \
  {0x00E1, 18584, 1422, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 18584, 1422, 0x0400}, \
  {0x00E1, 18584, 1422, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 18584, 1422, 0x0400}, \
  {0x00E1, 18584, 1422, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 18584, 1422, 0x0400}, \
  {0x00E1, 18584, 1422, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \

#include "fsk24rx.c"
