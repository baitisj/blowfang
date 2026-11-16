/* jm_glue.c: rt_api_t (radios/rt950/src/rt_api.h) on jaymart1983's RT-950 Pro firmware, the
   base blowfang builds on (github.com/jaymart1983/Radtel-RT950-Pro-Firmware, GPL-3, pinned
   in radios/rt950/README.md). It goes into that tree's src/app/ with surf2.c and
   c/common/surf_core.inc, and a menu entry calls rt950_surf2(). CI compiles it against the
   pinned checkout (.github/workflows/rt950.yml) so the calls below stay true to its drivers;
   it has not run on a radio (Andrew has the only one).

   What it relies on from that firmware, all verified there on hardware unless marked:
   - bk4829_write_reg / bk4829_read_reg: the bit-banged bus on PE10 / PE11 with chip selects PE8
     (chip 0) and PE15 (chip 1). Each call is one 24-bit frame; this file wraps every call in
     an interrupt-off section so nothing else on the bus (the firmware's own tasks don't run
     while SURF loops, but its timer interrupts do) can split a frame.
   - bk4829_set_frequency: REG_38 / REG_39 in 10 Hz units, REG_30 0x0200, ~52 us, REG_30 0xBFF1.
   - rx_path_receive: tunes chip 0 and sets the band relay and LNA (PE12 VHF, PE13 UHF, PB0).
     Chip 1's tunes leave the relays alone: it hears through chip 0's front end (unverified:
     docs/rt950-plan.md, open question 3).
   - REG_67: RSSI in the low 9 bits. The firmware measured its floor at 76-82 and a strong
     signal at 196-226 (src/app/rxgate.c); as BK4819 family dBm that is reg / 2 - RT_RSSI_OFS
     with the offset still to calibrate against the IC-7100 (README, "What needs the radio").
   - audio_speaker_mute / unmute: the speaker amplifier, which the firmware's rxgate owns
     otherwise; it doesn't run while SURF loops.
   - keypad_get_event: the keypad codes its header lists as checked key by key on the radio.
   - The bootloader leaves the watchdog running: key() feeds it, and the app calls key() every
     few milliseconds.
   - lcd_fill_rect / lcd_draw_string: the ST7789V, 240 x 320, 8 x 8 font.
   SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdint.h>
#include "at32f403a.h"
#include "cortex_m4.h"
#include "debug_uart.h"
#include "drivers/bk4829.h"
#include "drivers/lcd.h"
#include "app/audio.h"
#include "app/keypad.h"
#include "app/rx_path.h"
#include "rt_api.h"
#include "surf2.h"

#ifndef RT_RSSI_OFS
#define RT_RSSI_OFS 160         /* REG_67 / 2 - 160 dBm: the BK4819's; to calibrate on the BK4829 */
#endif
#define CPU_HZ 120000000u       /* the firmware's clock */
#define IWDG_FEED() (*(volatile uint32_t *)0x40003000UL = 0x0000AAAAUL)

extern uint32_t get_tick(void);

/* screen: 240 wide, 320 tall; the bars in a band under four text lines */
#define BAR_X 3                 /* 63 bins x 3 px + 3 = 192 ... centred below */
#define BAR_W 3
#define BAR_Y 80
#define BAR_H 150
#define C_BG 0x0000u
#define C_FG 0xFFFFu
#define C_BAR 0x07E0u           /* green */
#define C_LISTEN 0x07FFu        /* cyan: bins chip 0 fills */
#define C_HELD 0x4208u          /* grey: the guard's unread bins */
#define C_CARET 0xFFE0u         /* yellow */

static void say(const char *line) {
  (void)line;                   /* (dbg_puts compiles to nothing without DEBUG=1) */
  dbg_puts(line);
  dbg_newline();
}

static uint32_t ms(void) { return get_tick(); }

/* a busy wait: the DWT cycle counter, which the Cortex-M4 has */
static void delay_us(uint32_t us) {
  volatile uint32_t *dwt_ctrl = (volatile uint32_t *)0xE0001000u;
  volatile uint32_t *dwt_cyc = (volatile uint32_t *)0xE0001004u;
  volatile uint32_t *demcr = (volatile uint32_t *)0xE000EDFCu;
  uint32_t t0, n = us * (CPU_HZ / 1000000u);
  *demcr |= 1u << 24;
  *dwt_ctrl |= 1u;
  t0 = *dwt_cyc;
  while (*dwt_cyc - t0 < n) {
  }
}

static uint16_t rf_read(int chip, int reg) {
  uint16_t v;
  __disable_irq();
  v = bk4829_read_reg((uint8_t)chip, (uint8_t)reg);
  __enable_irq();
  return v;
}

static void rf_write(int chip, int reg, uint16_t v) {
  __disable_irq();
  bk4829_write_reg((uint8_t)chip, (uint8_t)reg, v);
  __enable_irq();
}

static void rf_tune(int chip, uint32_t hz) {
  __disable_irq();
  if (chip == RT_CHIP_LISTEN) rx_path_receive(hz);
  else bk4829_set_frequency((uint8_t)chip, hz);
  __enable_irq();
}

static int rf_dbm(int chip) { return (int)((rf_read(chip, 0x67) & 0x1FFu) >> 1) - RT_RSSI_OFS; }

static void rf_off(int chip) { rf_write(chip, 0x30, 0x0000); }

static void speaker(int on) {
  if (on) audio_speaker_unmute();
  else audio_speaker_mute();
}

/* the RT-950's keys onto SURF's: arrows and the side keys as on the K6, OK = MENU (say),
   Back = EXIT, V/M = BAND, ABC = A/B (the carets' fall); PTT is not on the matrix (PE3) and
   reaches SURF only through a firmware TX path, so it isn't mapped here yet */
static int key(void) {
  static const char MAP[KEY_COUNT] = {
      '1',                      /* KEY_1 */
      '2',                      /* KEY_2 */
      '3',                      /* KEY_3 */
      'W',                      /* KEY_A_VFO: ABC */
      '4',                      /* KEY_4 */
      '5',                      /* KEY_5 */
      '6',                      /* KEY_6 */
      'X',                      /* KEY_B_SCAN: Back */
      '7',                      /* KEY_7 */
      '8',                      /* KEY_8 */
      '9',                      /* KEY_9 */
      'M',                      /* KEY_C_MENU: OK */
      '*',                      /* KEY_STAR */
      '0',                      /* KEY_0 */
      '#',                      /* KEY_HASH */
      'N',                      /* KEY_D_BAND: V/M */
      'U',                      /* KEY_C4R0: up */
      'D',                      /* KEY_C4R1: down */
      'D',                      /* KEY_C4R2: left */
      'U',                      /* KEY_C4R3: right */
      'A',                      /* KEY_SIDE1 */
      'B',                      /* KEY_SIDE4 */
  };
  key_event_t e;
  IWDG_FEED();
  if (!keypad_get_event(&e)) return 0;
  if (e.type != KEY_EVT_PRESS && !(e.type == KEY_EVT_REPEAT && (e.key == KEY_C4R0 || e.key == KEY_C4R1)))
    return 0;
  return e.key < KEY_COUNT ? MAP[e.key] : 0;
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v, int dot) {
  char t[11];
  int n = 0;
  do {
    t[n++] = (char)('0' + v % 10u);
    v /= 10u;
  } while (v || n <= dot);
  while (n) {
    if (dot && n == dot) *p++ = '.';
    *p++ = t[--n];
  }
  return p;
}

static void show(const rt_view_t *v) {
  char ln[32], *p;
  int i;
  uint16_t x0 = (uint16_t)((240 - BAR_W * SURF2_NV) / 2);
  p = put(ln, v->band);
  *p++ = ' ';
  p = put(p, v->mode);
  p = put(p, v->mon ? " MON" : v->hold ? " HLD" : v->ptt ? " TX" : "    ");
  *p = 0;
  lcd_draw_string(4, 4, ln, C_FG, C_BG);
  p = dec(ln, v->cen / 100u, 4);
  p = put(p, v->audio ? " RX" : "   ");
  *p = 0;
  lcd_draw_string_2x(4, 20, ln, C_FG, C_BG);
  p = put(ln, v->dbm < 0 ? "-" : "");
  p = dec(p, (uint32_t)(v->dbm < 0 ? -v->dbm : v->dbm), 0);
  p = put(p, " dBm  span ");
  p = put(p, v->span_txt);
  p = put(p, "   ");
  *p = 0;
  lcd_draw_string(4, 44, v->msg ? v->msg : ln, C_FG, C_BG);
  for (i = 0; i < SURF2_NV; i++) {
    uint16_t x = (uint16_t)(x0 + BAR_W * i), h = 0, col;
    if (v->hz[i] && v->lvl[i] > v->floor_q) h = (uint16_t)((v->lvl[i] - v->floor_q) * 3u);
    if (h > BAR_H) h = BAR_H;
    if (h < 1) h = 1;
    col = v->flags[i] & SURF2_LISTEN ? C_LISTEN : v->flags[i] & SURF2_HELD ? C_HELD : C_BAR;
    lcd_fill_rect(x, BAR_Y, BAR_W - 1, (uint16_t)(BAR_H - h), C_BG);
    lcd_fill_rect(x, (uint16_t)(BAR_Y + BAR_H - h), BAR_W - 1, h, v->hz[i] ? col : C_BG);
    if (v->caret[i])
      lcd_fill_rect(x, (uint16_t)(BAR_Y + BAR_H - 2 - (v->caret[i] * BAR_H) / 160u), BAR_W - 1, 2, C_CARET);
  }
  lcd_fill_rect((uint16_t)(x0 + BAR_W * 31), (uint16_t)(BAR_Y + BAR_H + 2), BAR_W - 1, 6, C_FG);
}

static const rt_api_t API = {
    RT_API_LEVEL,
    say,
    ms,
    delay_us,
    rf_read,
    rf_write,
    rf_tune,
    rf_dbm,
    rf_off,
    speaker,
    key,
    show,
};

/* the menu entry: SURF from the VFO's frequency, until Back; the radio then listens there again
   and its own tasks take over */
int rt950_surf2(uint32_t vfo_hz) {
  static uint32_t ram[1024];    /* 4 KB: the state, a frame and 128 remembered peaks need ~2.9 KB */
  lcd_fill_rect(0, 0, 240, 320, C_BG);
  return surf2_run(&API, ram, sizeof ram, vfo_hz);
}
