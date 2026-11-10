/* aprsfskb: aprsfsk.c reading the modem's FIFO all the time (FSK_STREAM): no sync flag needed, and
   the search is not started again every 59 ms. SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define APRS_FSK
#define FSK_STREAM
#include "aprsclk.c"
