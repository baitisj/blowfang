/* dfskcap_acarsc: dfskcap.c's ACARS recorder with the plain FSK receiver's untried settings
   one after the other (DFSK_SETS), in FM on 440.850 MHz for the bench. Eight at 2400 baud,
   where the receiver would read the block's bytes as they are if its clock settles between
   the audio's turns (the slope it slices is the data bit at a bit's edge); then the fastest
   clock (REG_72 = ffff), and at 4800 baud REG_58's RX bandwidth bits, its bits 7:6 and an
   unnamed RX mode. The clock sweep (dfskcap_acarsb) found the chip's clock locked onto the
   signal from 5200 to 6300 baud and no block readable there. The IC-7100 sends real blocks
   (python3 -m modem.acars probe --only R) in FM at power 0; sixteen settings of 11 s from
   60 s after the start, so the bench sends the same signal at the same times as for
   dfskcap_acarsb. Receive only. docs/k6-acars.md. SPDX-License-Identifier: MIT */
#define DFSK_ACARS
#define DFSK_SETS
#define DFSK_DUMP_ALL
#define PROBE_HZ 440850000u
#define WAIT_MS 60000u
#define FE_S 11
#include "dfskcap.c"
