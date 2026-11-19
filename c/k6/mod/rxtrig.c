/* rxtrig: a carrier trigger for bench captures (docs/k6-c4fm.md, 11-18). Receive only. The K6
   listens where the arena has it (440.850) and says a line the moment a carrier comes up, so a
   bench job can start a Pluto capture on another radio's key-up: the FTM-300DR's own voice call
   for c4voice, which the Pluto (no trigger of its own) would otherwise have to record for minutes.
   Lines:
     rxtrig armed f=440850000    the upload's carrier gone for RT_CLEAR_MS: listening
     rxtrig on -63               a carrier, RSSI at or over RT_DBM for RT_ON_MS (its dBm)
     rxtrig off 5230 -58         gone for RT_OFF_MS: how long it lasted (ms), its strongest dBm
   It returns the carriers heard after RT_N of them, RT_WAIT_MS of listening, or a key (-4).
   Build: python -m modem.k6 mod out.wav c/k6/mod/rxtrig.c --key-file KEY.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2   /* fd_read, rx_hz, ms */
#include "../k6_mod.h"

#ifndef RT_DBM
#define RT_DBM -90          /* as c4lock's C4_CLEAR_DBM */
#endif
#define RT_CLEAR_MS 2000u
#define RT_ON_MS 60u
#define RT_OFF_MS 500u
#ifndef RT_N
#define RT_N 3
#endif
#ifndef RT_WAIT_MS
#define RT_WAIT_MS 1800000u /* 30 minutes */
#endif
K6_MODULE_NAME("RX TRIGGER");

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {     /* no divide on the M0: subtract powers of ten */
  static const uint32_t pw[] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u,
                                10000u, 1000u, 100u, 10u, 1u};
  int i, started = 0;
  for (i = 0; i < 10; i++) {
    char d = '0';
    while (v >= pw[i]) { v -= pw[i]; d++; }
    if (d != '0' || started || i == 9) { *p++ = d; started = 1; }
  }
  return p;
}

static char *sdec(char *p, int32_t v) {
  if (v < 0) { *p++ = '-'; v = -v; }
  return dec(p, (uint32_t)v);
}

int k6_main(const k6_api_t *api) {
  if (api->level < 2) return -3;
  char line[64], *p;
  uint32_t quiet = api->ms(), up = 0, down = 0, t_on = 0;
  int armed = 0, on = 0, n = 0, peak = -174;
  for (;;) {
    uint32_t t = api->ms();
    int dbm = KM_RSSI_DBM(api->fd_read(0x67));
    if (dbm >= RT_DBM) {
      quiet = t;
      if (!up) up = t;
      if (dbm > peak) peak = dbm;
    } else {
      up = 0;
    }
    if (!armed) {
      if (t - quiet >= RT_CLEAR_MS) {
        armed = 1;
        p = dec(put(line, "rxtrig armed f="), api->rx_hz());
        *p = 0;
        api->say(line);
      }
    } else if (!on && up && t - up >= RT_ON_MS) {
      on = 1;
      t_on = up;
      p = sdec(put(line, "rxtrig on "), dbm);
      *p = 0;
      api->say(line);
    } else if (on && t - quiet >= RT_OFF_MS) {
      on = 0;
      down = quiet - t_on;
      p = sdec(put(dec(put(line, "rxtrig off "), down), " "), peak);
      *p = 0;
      api->say(line);
      if (++n >= RT_N) return n;
    }
    if (!on) peak = up ? peak : -174;
    if (t > RT_WAIT_MS) return n;
    if (api->key_down()) return -4;
    api->delay_us(10000);
  }
}
