/* settings: the radio's own settings (module API level 10). Jeff 2026-11-17: "the backlight
   remains on the entire time, draining battery", and "Probably need this as part of a Settings
   module". The core image keeps the backlight's idle timeout (Aura's src/hal/light.rs in
   firmware/k6-aura/aura-pager.patch); this module picks the delay and saves it in Aura's
   settings record, so it holds after a power cycle and a reflash.

     SETTINGS
     > LIGHT OFF:  10 S         UP / DOWN: always on, 5, 10, 15, 20, 30 s, 1 min, 2 min
       (what the value does)
     EXIT: SAVE + BACK

   A new value works at once (the light now goes out after it), so it can be tried before it
   is kept. EXIT saves it, if it changed, and leaves; two minutes with no key do the same. Any
   key lights the screen again, and so do a message coming in, a message playing and PTT.

   Serial: "settings light=N secs=S saved" (or "unchanged"). Returns the setting (0-7), -1 if
   the core refused the save, -3 on an image before level 10 (the header's level stops it there
   first). SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 10
#include "../k6_mod.h"
K6_MODULE_NAME("SETTINGS");
#include "k6ui.h"

#define IDLE_MS 120000u

/* the backlight's delays (the core's hal::light::SECS) and how they read on the screen */
static const uint8_t SECS[KM_LIGHT_MAX + 1] = {0, 5, 10, 15, 20, 30, 60, 120};
static const char SHOW[KM_LIGHT_MAX + 1][6] = {"NEVER", "5 S",  "10 S", "15 S",
                                               "20 S",  "30 S", "1 MIN", "2 MIN"};

static void draw(ui_t *u, int v) {
  char l[24];
  ui_put(ui_put(l, "> LIGHT OFF: "), SHOW[v]);
  ui_text(u, 1, l);
  ui_text(u, 2, v ? "  AFTER NO KEY OR MSG" : "  ALWAYS ON");
}

int k6_main(const k6_api_t *api) {
  ui_t u;
  char l[48], *p;
  int v, v0, k, r;
  uint32_t t;
  if (api->abi != 1 || api->level < 10) return -3;
  v = v0 = api->setting(KM_SET_LIGHT, -1, 0);
  if (v < 0 || v > KM_LIGHT_MAX) v = v0 = 2;
  ui_begin(&u, api, "SETTINGS", "");
  ui_text(&u, 4, "UP/DOWN: CHANGE");
  ui_text(&u, 5, "EXIT: SAVE + BACK");
  draw(&u, v);
  ui_now(&u);
  ui_release(&u);  /* the key that started it */
  t = api->ms();
  while (u.on && api->ms() - t < IDLE_MS) {
    k = ui_key(&u);
    if (k == 'X') break;
    if (k == 'U' || k == 'D') {
      v = k == 'U' ? (v == KM_LIGHT_MAX ? 0 : v + 1) : (v == 0 ? KM_LIGHT_MAX : v - 1);
      api->setting(KM_SET_LIGHT, v, 0);  /* at once: the light goes out after the new delay */
      draw(&u, v);
    }
    if (k) t = api->ms();
    ui_flush(&u, 50);
    api->delay_us(10000);
  }
  r = v == v0 ? v : api->setting(KM_SET_LIGHT, v, 1);
  p = ui_dec(ui_put(l, "settings light="), (uint32_t)v, 1);
  p = ui_dec(ui_put(p, " secs="), SECS[v], 1);
  ui_put(p, v == v0 ? " unchanged" : r < 0 ? " save refused" : " saved");
  api->say(l);
  if (u.on) {
    ui_text(&u, 4, "");
    ui_text(&u, 5, v == v0 ? "UNCHANGED" : r < 0 ? "NOT SAVED" : "SAVED");
    ui_now(&u);
    api->delay_us(600000);
  }
  return r;
}
