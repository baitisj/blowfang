/* samecap_nwr: samecap.c on NOAA Weather Radio, 162.550 MHz (KHB60 Seattle), the datasheet's SAME
   setting alone (REG_58 00e5, sync d5d5) for an hour from the start: the weekly test (Wednesdays,
   about midday Pacific) or any alert. Only captures that go on like a preamble are said. Receive only; a weather broadcast, not a protected one.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#define SAME_HZ 162550000u
#define WAIT_MS 0u
#define NV 1
#define FE_S 3600u
#define SAME_PRE
#define CAPN 24u
#include "samecap.c"
