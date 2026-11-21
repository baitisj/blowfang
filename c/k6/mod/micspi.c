/* micspi: micsotto (micsotto.c) that writes its superframes into the SPI outbox as they come,
   so a take runs up to REC_MS (60 s) instead of the 3 s the RAM holds (2026-11-21, Jeff: "it's
   time to record to SPI"). Level 15, whose fl_prog takes an outbox page while keyed with the PA
   off. No cue: after the CW call the screen says TALK NOW, and any key ends the take. The
   outbox's header goes last, in outbox.h's layout, so fusotx.c can send the take as FuSotto.
   After the replug the hex lines are read back from the outbox; the first 20 s play.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 15
#define K6_MODULE_OPT z
#define MS_SPI 1
#include "micsotto.c"
