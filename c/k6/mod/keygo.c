/* keygo: a bench job's go button on the K6 (2026-11-20, Jeff: "'Arm' means to get ready to go,
   not 'go'"). A job uploads this, then waits for its "keygo go" line before it sends anything
   else. It arms once the upload's carrier has gone (1 s below -90 dBm), shows ARMED and "ANY KEY:
   GO" on the screen, and returns at the release of the first key press after that, saying
   "keygo go". A key held through the arming does not count: it must be let go and pressed again.
   It keys nothing and writes no register.

   Lines: "keygo armed", then "keygo go" (or "keygo timeout"). Returns 1 on the go, -7 if no go
   within KEYGO_WAIT_MS. The screen needs level 6; below that it only says its lines. Level 4.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 4
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"

#ifndef KEYGO_WAIT_MS
#define KEYGO_WAIT_MS 2400000u /* 40 minutes */
#endif
#ifndef KEYGO_CLEAR_MS
#define KEYGO_CLEAR_MS 1000u
#endif
#define KEYGO_CLEAR_DBM -90

static void show(const k6_api_t *api, const char *l1, const char *l5) {
  if (api->level < 6) return;
  api->lcd_text(0, "KEYGO");
  api->lcd_text(1, l1);
  api->lcd_text(5, l5);
  api->lcd_flush();
}

int k6_main(const k6_api_t *api) {
  uint32_t t0 = api->ms(), quiet = t0;
  int armed = 0, held = 1;
  for (;;) {
    uint32_t t = api->ms();
    int k = api->key_poll();
    if (t - t0 >= KEYGO_WAIT_MS) {
      api->say("keygo timeout");
      show(api, "NO GO: TIMED OUT", "");
      return -7;
    }
    if (!armed) {
      if (KM_RSSI_DBM(api->fd_read(0x67)) >= KEYGO_CLEAR_DBM) quiet = t;
      if (t - quiet >= KEYGO_CLEAR_MS) {
        armed = 1;
        held = k; /* a key already down must be let go first */
        api->say("keygo armed");
        show(api, "ARMED", "ANY KEY: GO");
      }
    } else if (k) {
      if (held < 2) held = held ? 1 : 2;
    } else if (held == 2) {
      api->say("keygo go");
      show(api, "GO", "");
      return 1;
    } else {
      held = 0;
    }
    api->delay_us(10000);
  }
}
