/* surf2.h: two-chip SURF on the RT-950 Pro (radios/rt950/src/surf2.c).
   SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef SURF2_H
#define SURF2_H

#include <stdint.h>
#include "rt_api.h"

#define SURF2_NV 63             /* bins on screen: the middle (the listening frequency) and 31 each side */

/* bin flags */
#define SURF2_LISTEN 1          /* in the guard round the listener: chip 0's level, not chip 1's read */
#define SURF2_HELD 2            /* in the guard but past the listener's filter: nobody reads it, it decays */

/* one frame, for the glue to draw (port/jm_glue.c on the ST7789, sim/rt950_sim.c as text) */
struct rt_view {
  const char *band;             /* "70cm" */
  const char *mode;             /* "FM", "DSB", "AM" */
  const char *msg;              /* a key's note ("no peak", "gain -12 dB"), or 0 */
  const char *tune;             /* TUNE's typing with its cursor ("446.5_"), or 0 when not typing */
  uint32_t step;                /* channel step, Hz */
  uint32_t cen;                 /* the listening frequency (chip 0), Hz */
  uint32_t span;                /* the last bin's distance from the middle, Hz */
  char span_txt[8];             /* it as the K6's axis labels it: "100k", "2.5M" */
  uint32_t sweep_hz;            /* where chip 1 is now */
  uint32_t sweeps;
  int dbm;                      /* chip 0's level, dBm */
  int floor_dbm;                /* chip 0's settled floor, dBm */
  int gain_db;                  /* fixed gain in dB, or 1 for the AGC */
  uint8_t audio;                /* the speaker is open */
  uint8_t mon;
  uint8_t hold;
  uint8_t ptt;                  /* transmitting: the sweep stopped and chip 1 off */
  uint8_t floor_q;              /* the bars' floor, dBm + 174 */
  uint32_t hz[SURF2_NV];        /* each bin's frequency, 0 outside the band */
  uint8_t lvl[SURF2_NV];        /* each bin's bar, dBm + 174 on chip 1's quick scale, 0 unknown */
  uint8_t caret[SURF2_NV];      /* the highest caret over a bin, x 1/8 px (152 the top), 0 none */
  uint8_t flags[SURF2_NV];      /* SURF2_LISTEN, SURF2_HELD */
};

/* Runs until EXIT ('X'), starting on hz (the radio's VFO). ram is the work space (at least
   surf2_ram() bytes; more holds more remembered peaks, up to 128). Returns the whole sweeps done,
   or -4 if ram is too small. Receive only: SURF never transmits. Key 'P' / 'p' (the glue's PTT
   down / up) stop the sweep and turn chip 1's front end off until PTT is released, the rule any
   two-chip app follows around a transmission (docs/rt950-plan.md). */
int surf2_run(const rt_api_t *api, void *ram, uint32_t room, uint32_t hz);
uint32_t surf2_ram(void);

#endif
