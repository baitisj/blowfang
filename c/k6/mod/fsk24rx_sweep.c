/* fsk24rx_sweep: the K6's FSK receive sensitivity at 2400, 1800 and 1200 baud, fsk24rx.c run as
   slots of 12 s on a fixed grid: D/2400 (run 3's setting 2), F/1800 and F/1200 (the pager's
   recipes) in turn, 36 slots. The ADALM-Pluto sends each slot its rate's test packets, one
   level step lower every three slots, 0 to -44 dB in 4 dB steps, timed from the "s 0" line
   (python3 tools/pluto_bench.py fsk24 --sweep --follow LOG). Report: python3 -m modem.fsk24
   report --sweep LOG. The table below is python3 -m modem.fsk24 sweep-table (a test checks it).
   SPDX-License-Identifier: MIT */
#define FE_S 12u
#define F24_ABS 1
#define WAIT_MS 30000u
#define DUMPN 1u
#define F24_NV 36
#define F24_V \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x3F01, 18584, 1422, 0x0000}, \
  {0x3FE3, 12389, 2133, 0x0000}, \

#include "fsk24rx.c"
