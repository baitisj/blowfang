/* k6_stop.h: the PC's stop signal for a long-running module, with no firmware change (insight 31
   in docs/k6-modules.md: a WSPR beacon once held the bench for 50 minutes, because the pager
   reads no control packets while a module runs and only a key on the radio could end it).

   The signal is two short carriers on the module's receive frequency: `tools/k6_bench.py stop`
   keys the IC-7100 twice, each time for a PSK31 "AG7EW STOP" burst (about 3.5 s on air, so it
   carries the ident), with about 2 s between. A module feeds the receiver's RSSI to
   k6stop_feed() from its wait loops, every 5-250 ms, and stops when it returns 1. Nothing else
   on the bench makes that pattern: a CW ID, a sync mark, a data send or a module upload is one
   carrier of 6 s or more (a CW ID is about 10 s), and a kerchunk is under a second.

     carrier  |------ 2-6 s ------|  1-5 s  |------ 2-6 s ------|  0.3 s quiet -> stop
     RSSI     >= K6STOP_DBM                                        (dropouts under 0.3 s ignored)

   The state is the caller's (a module has no writable statics): zero it once, then feed it.
   No divide, no 64-bit arithmetic. SPDX-License-Identifier: MIT */
#ifndef K6_STOP_H
#define K6_STOP_H
#include <stdint.h>

#ifndef K6STOP_DBM
#define K6STOP_DBM -90        /* a carrier: the bench's IC-7100 reads -40 to -60 at the K6 */
#endif
#define K6STOP_ON_MIN 2000u   /* ms, each carrier */
#define K6STOP_ON_MAX 6000u
#define K6STOP_GAP_MIN 1000u  /* ms, between them */
#define K6STOP_GAP_MAX 5000u
#define K6STOP_DROP 300u      /* ms of quiet before a carrier counts as gone */

typedef struct {
  uint32_t t_on;    /* when the carrier came up */
  uint32_t t_seen;  /* the last time it was up */
  uint8_t on;       /* a carrier is up (or dropped under K6STOP_DROP ago) */
  uint8_t n;        /* 1: one carrier of the right length ended at t_seen */
} k6stop_t;

/* one RSSI reading (dBm) at ms; 1 when the second carrier of the pattern has just ended */
static int k6stop_feed(k6stop_t *s, uint32_t ms, int dbm) {
  if (dbm >= K6STOP_DBM) {
    if (!s->on) {
      if (s->n && (ms - s->t_seen < K6STOP_GAP_MIN || ms - s->t_seen > K6STOP_GAP_MAX))
        s->n = 0;                                 /* too soon or too late after the first */
      s->on = 1;
      s->t_on = ms;
    }
    s->t_seen = ms;
    return 0;
  }
  if (s->on) {
    uint32_t len;
    if (ms - s->t_seen < K6STOP_DROP) return 0;
    s->on = 0;
    len = s->t_seen - s->t_on;
    if (len < K6STOP_ON_MIN || len > K6STOP_ON_MAX) {
      s->n = 0;
      return 0;
    }
    if (s->n) {
      s->n = 0;
      return 1;
    }
    s->n = 1;
    return 0;
  }
  if (s->n && ms - s->t_seen > K6STOP_GAP_MAX) s->n = 0;      /* no second carrier in time */
  return 0;
}

#endif
