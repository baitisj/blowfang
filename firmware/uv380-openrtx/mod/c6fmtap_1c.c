/* c6fmtap_1c: c6fmtap held at 0x34 = 0x1C (band-pass, expander and de-emphasis off, 25 kHz RX
   bandwidth): the setting that reads flat from 10 Hz to 3.4 kHz and decodes M17 (run
   m17rx-tap-1118a, docs/uv380-m17-rx.md). 150 s, for BER against the Pluto's level.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define TAP_V34 0x1C
#define TAP_S 150u
#define TAP_STEP_MS 1000000u
#include "c6fmtap.c"
