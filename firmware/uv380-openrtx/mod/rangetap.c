/* rangetap: rangerx with a tap. What it hands the receiver also goes to the USB serial as
   rxrec's "a SEQ hhhh ..." lines (a line per read of the ring, up to 64 samples), so
   tools/uv380_i2sprobe.py audio and c/common/range_rx_host can read what the receiver on the
   radio was given, beside what it made of it. Same lines and run as rangerx otherwise.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#define RANGERX_TAP 1
#include "rangerx.c"
