/* aprsdfskt: aprsdfsk.c reading the chip's FIFO by time, as the probe dfskcap did (DF_MODE 1),
   and dropping the reads that found it empty. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_PLAIN
#define DF_MODE 1
#include "aprsclk.c"
