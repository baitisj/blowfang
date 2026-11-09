/* xdmrlisten: EXPLORE's Listen for ID with its DMR detector (c/k6/xlisten.c + c/k6/xdmr.c, the
   same code the EXPLORE image links), as a RAM module, so the detector can be measured and
   changed on the radio without a flash (2026-11-09: on txdog images it resyncs at every wait,
   `DMR end ... lost=` 30 a step). Listens RUN_S seconds on RX_HZ (440.925 MHz, the local DMR
   repeater) on the speaker, then says xd_stop's DMR end / rate / diag lines. Receive only.
   The DMA ring (XD_RSZ) and the two states live in the scratch, not on the ~3 KB stack a
   module call gets (dmrrx's first bench run overran it), so it needs a voice image's
   11,776-byte block (PR #146), not EXPLORE's 5,376. -6 and a "xdmr scratch" line when short.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define XD_RING_EXT 1
#ifndef RUN_S
#define RUN_S 60u
#endif
#ifndef RX_HZ
#define RX_HZ 440925000u
#endif
#include "../k6_mod.h"
#include "../cwid.c"
#include "../xlisten.c"
#include "../xdmr.c"

int k6_main(const k6_api_t *api) {
  uint32_t have = 0, need = XD_RSZ + ((sizeof(xd_t) + 3) & ~3u) + ((sizeof(xl_t) + 3) & ~3u);
  uint8_t *m = (uint8_t *)api->scratch(&have);
  xl_t *x;
  xd_t *d;
  int r;
  if (!m || have < need) {
    char line[40], *p = xd_put(line, "xdmr scratch ");
    p = xd_put(cw_utoa(p, have), " need ");
    *cw_utoa(p, need) = 0;
    api->say(line);
    return -6;
  }
  d = (xd_t *)(void *)(m + XD_RSZ);
  x = (xl_t *)(void *)(m + XD_RSZ + ((sizeof(xd_t) + 3) & ~3u));
  xl_start(x, api, RX_HZ, RUN_S);
  xd_start(d, api, 1, 0);
  d->ring = (uint32_t *)(void *)m;
  while ((r = xd_step(d, x)) != XL_TIME && r != XL_OK) {}
  r = xd_stop(d);
  xl_stop(x, 2);
  return r;
}
