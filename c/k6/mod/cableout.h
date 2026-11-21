/* cableout.h: a K6 module that runs with the programming cable pulled out of the jack (2026-11-20,
   Jeff: the cable takes the mic out). Nothing said meanwhile reaches the logger, so the screen
   tells the person at the bench what to do, and a whole key press says it is done.
   SPDX-License-Identifier: MIT */
#ifndef K6_CABLEOUT_H
#define K6_CABLEOUT_H

/* three lines of the screen (level 6 and up; below that nothing) */
static void co_show(const k6_api_t *api, const char *title, const char *l1, const char *l2) {
  if (api->level < 6) return;
  api->lcd_text(0, title);
  api->lcd_text(2, l1);
  api->lcd_text(3, l2);
  api->lcd_flush();
}

/* a whole key press (a key held now must be let go first): 0, or -7 after ms */
static int co_key(const k6_api_t *api, uint32_t ms) {
  uint32_t t0 = api->ms();
  int phase;
  for (phase = 0; phase < 3; phase++) /* let go, press, let go */
    while (api->key_poll() == !(phase & 1)) {
      if (api->ms() - t0 >= ms) return -7;
      api->delay_us(20000);
    }
  return 0;
}

#endif
