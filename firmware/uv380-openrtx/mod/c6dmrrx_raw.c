/* c6dmrrx in the physical layer test mode (0x10 = 0x02, 0x40 = 0x40, 0x41 = 0x41): every
   demodulated 288-bit burst from RX RAM 0x30 as "r" lines, no layer 2 decoding. The C6000 as a
   general 4FSK demodulator (docs/uv380-c6000-modem.md).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define RX_RAW 1
#define RX_MS 20000u
#include "c6dmrrx.c"
