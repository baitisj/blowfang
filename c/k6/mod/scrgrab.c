/* scrgrab: the K6's screen on the serial report (module API level 17: screen_tap, redraw). The
   ST7565 can't be read back and the core keeps no copy of the screen, so this module taps the
   pages the core sends, has it draw its own screen again (what comes back when the module
   returns: VOICE MSG, the MENU, ...), keeps the 1 KB in its scratch and prints it in
   c/common/scrgrab.h's lines: format 3 (the ST7565's pages), one LZ4-packed band, CRC-32.
   tools/screen_grab.py turns the "K6 MOD say scr ..." lines of the serial log into a PNG:

     python3 tools/screen_grab.py LOG -o k6.png

   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 17
#define SCR_HEX_LINE 48                   /* say() prints 60 characters */
#include "../k6_mod.h"
#include "../../common/scrgrab.c"

typedef struct {
  uint8_t img[8 * 128];
  unsigned pages;                         /* a bit for each page seen */
} sg_t;

static void sg_page(int page, const uint8_t *bytes, void *ctx) {
  sg_t *g = (sg_t *)ctx;
  if (page < 0 || page > 7) return;
  for (int i = 0; i < 128; i++) g->img[page * 128 + i] = bytes[i];
  g->pages |= 1u << page;
}

int k6_main(const k6_api_t *api) {
  uint32_t room = 0;
  uint8_t *ram;
  if (api->level < 17) {
    api->say("scrgrab: this image has no screen tap (level 17)");
    return -1;
  }
  ram = (uint8_t *)api->scratch(&room);
  sg_t *g = (sg_t *)ram;
  uint8_t *pack = ram + sizeof(sg_t);
  uint16_t *tab = (uint16_t *)(pack + sizeof g->img);
  if (!ram || room < (uint32_t)((uint8_t *)(tab + LZ4C_TAB) - ram)) {
    api->say("scrgrab: not enough scratch");
    return -2;
  }
  g->pages = 0;
  if (api->screen_tap(sg_page, g) < 0) {
    api->say("scrgrab: this image doesn't draw a page at a time");
    return -1;
  }
  int r = api->redraw();
  api->screen_tap(0, 0);
  if (r < 0 || g->pages != 0xFF) {
    api->say(r == -2 ? "scrgrab: not over EXPLORE" : "scrgrab: the redraw sent no whole screen");
    return r < 0 ? r : -3;
  }
  scr_t s;
  s.say = api->say;
  s.band = g->img;                        /* the band is the screen: scr_row copies in place */
  s.pack = pack;
  s.tab = tab;
  scr_begin(&s, 128, 64, SCR_ST7565, 128, 8);
  for (int p = 0; p < 8; p++) scr_row(&s, g->img + 128 * p);
  scr_end(&s);
  return 8;
}
