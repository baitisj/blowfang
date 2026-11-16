/* rt_api.h: what blowfang's RT-950 Pro apps ask of the radio underneath them.

   The radio is Radtel's RT-950 Pro: an Artery AT32F403A (Cortex-M4F) and two BK4829
   transceivers on one bit-banged SPI bus (clock PE10, data PE11, chip selects PE8 for chip 0
   and PE15 for chip 1). The firmware underneath is jaymart1983/Radtel-RT950-Pro-Firmware
   (GPL-3, chosen 2026-11-16 over Hertzz58's, which it forked: see docs/rt950-plan.md), and
   port/jm_glue.c fills this table from its drivers. sim/rt950_sim.c fills it from a model of
   the two chips, so the apps run and are tested on a PC before anyone has the radio.

   Chip 0 is the one with audio (the firmware keeps chip 1 off the audio bus, REG_47 and REG_48
   zero), so in two-chip SURF chip 0 listens and chip 1 sweeps.

   Every call is one whole bus transaction: the glue does a register sequence (a tune is REG_38,
   REG_39, REG_30 0x0200, about 52 us, REG_30 0xBFF1) without letting anything else onto the
   bus in the middle, so the two chips' traffic never interleaves.

   Keys come as the characters the K6's SURF already uses, so the shared code reads the same on
   both radios: 'U' 'D' up and down, 'A' 'B' the side keys, 'W' the caret fall, 'N' band, 'M'
   say, 'X' exit, and the digits, '*' and '#'. The glue maps the RT-950's keypad onto them.
   SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef RT_API_H
#define RT_API_H

#include <stdint.h>

#define RT_CHIP_LISTEN 0        /* chip 0, PE8: audio */
#define RT_CHIP_SWEEP 1         /* chip 1, PE15: no audio */

typedef struct rt_view rt_view_t;

typedef struct {
  uint16_t level;               /* this table's version: RT_API_LEVEL */
  void (*say)(const char *line);                          /* a log line (debug UART) */
  uint32_t (*ms)(void);                                   /* milliseconds, free running */
  void (*delay_us)(uint32_t us);
  uint16_t (*rf_read)(int chip, int reg);
  void (*rf_write)(int chip, int reg, uint16_t v);
  /* tune a chip for receive (10 Hz units in REG_38 and REG_39, then the REG_30 relock). For chip
     0 the glue also sets the front end's band relay and LNA (PE12 VHF, PE13 UHF, PB0); for chip
     1 it leaves them, so chip 1 hears through chip 0's front end (docs/rt950-plan.md, open
     question 3) */
  void (*rf_tune)(int chip, uint32_t hz);
  int (*rf_dbm)(int chip);                                /* REG_67 as dBm */
  void (*rf_off)(int chip);                               /* REG_30 0: the chip's front end off */
  void (*speaker)(int on);                                /* the speaker amplifier */
  int (*key)(void);                                       /* a key press as above, or 0 */
  void (*show)(const rt_view_t *v);                       /* draw a frame */
  /* level 2: transmit on chip 0, for src/digi_tx.c. NULL where the radio can't yet (the base
     firmware has no TX rung, so port/jm_glue.c leaves them NULL until it does) */
  uint32_t (*us)(void);                                   /* microseconds, free running */
  int (*tx_on)(uint32_t hz, int power);                   /* key up chip 0 on hz; 0, or < 0 refused */
  void (*tx_hz)(uint32_t hz);                             /* move the carrier while keyed (REG_38/39) */
  void (*tx_power)(int power);                            /* 0: carrier off, still keyed (CW) */
  void (*tx_off)(void);
} rt_api_t;

#define RT_API_LEVEL 2

#endif
