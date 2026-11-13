/* rangetx_lo: rangetx with the PA off while keyed, the lowest power the K6 can send: only the
   FD6818's leak gets out (about 40 dB under low power at bench range, docs/k6-own-fsk.md), for
   bench runs with the UV-380 a few feet away. Jeff asked for low-power K6 sends, 2026-11-13.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 3
#define RT_PA 0
#include "rangetx.c"
