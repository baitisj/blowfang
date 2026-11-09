/* fdtick: SysTick's interrupt off for good, then fdirq (FD_TICKOFF in fdtime.c, 2026-11-09).
   Receive only, writes no radio register. Level 2. SPDX-License-Identifier: MIT */
#define FD_TICKOFF
#include "fdtime.c"
