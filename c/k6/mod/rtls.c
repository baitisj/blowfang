/* rtls: list the K6 explorer's repeater table (c/k6/rt.h, docs/k6-repeater-table.md) over the
   serial report and the upload's telemetry. The first module on API level 5 (the SPI flash):

     K6 MOD say RT live=3 dead=1 free=956 spare=15 repairs=0 r=3
     K6 MOD say RT 1 146.940 -600000 T0 W7ABC/R R -79
     ...

   It mounts the table and reads it. It writes nothing, unless the mount finds what a cut power
   left half done (rt_mount finishes or undoes it, through fl_prog / fl_erase and the firmware's
   checks). A region that isn't blank and isn't a table is refused (r=-5) and left alone.
   Returns the live entries, or < 0 (rt.h's RT_ERR_*, -3 on an image below level 5).
   No libraries, no division, no writable statics. SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 5
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "../rt.c"
#ifndef K6_NAME
#define K6_NAME "STATION LIST"   /* its name in the MENU list (a file that includes this one may set its own) */
#endif
K6_MODULE_NAME(K6_NAME);
#include "k6ui.h"

static int fr(void *c, uint32_t a, void *b, uint32_t n) { return ((const k6_api_t *)c)->fl_read(a, b, n); }
static int fw(void *c, uint32_t a, const void *b, uint32_t n) { return ((const k6_api_t *)c)->fl_prog(a, b, n); }
static int fe(void *c, uint32_t a) { return ((const k6_api_t *)c)->fl_erase(a); }

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

/* decimal without a divide */
static char *dec(char *p, int32_t v) {
  static const uint32_t pw[] = {1000000000, 100000000, 10000000, 1000000, 100000, 10000, 1000, 100, 10, 1};
  uint32_t u;
  int i, started = 0;
  if (v < 0) *p++ = '-', v = -v;
  u = (uint32_t)v;
  for (i = 0; i < 10; i++) {
    int d = 0;
    while (u >= pw[i]) u -= pw[i], d++;
    if (d || started || i == 9) *p++ = (char)('0' + d), started = 1;
  }
  return p;
}

static char *field(char *p, const char *s, int n) {
  int i;
  for (i = 0; i < n && s[i]; i++) *p++ = s[i];
  return p;
}

/* the screen (an image with one): the stations, four at a time, "146.940 W7ABC/R -79"; UP and
   DOWN scroll, EXIT (or MENU) goes back */
__attribute__((noinline)) static void show(const k6_api_t *api, rt_t *t, int n) {
  ui_t u;
  rt_rec_t r;
  char l[64], *p;
  int top = 0, k = 1, i, slot;
  p = dec(put(l, "STATIONS "), n);
  *p = 0;
  ui_begin(&u, api, l, "");
  if (!u.on) return;
  ui_release(&u);
  for (;;) {
    if (k) {
      for (i = 0, slot = rt_next(t, -1, &r); i < top + 5; i++, slot = rt_next(t, slot, &r)) {
        if (i < top) continue;
        p = l;
        if (slot >= 0) {
          p = ui_mhz(l, r.rx_hz);
          *p++ = ' ';
          p = r.call[0] ? field(p, r.call, 10) : r.name[0] ? field(p, r.name, 8) : put(p, "-");
          *p++ = ' ';
          p = dec(p, (int32_t)r.best_rssi - 200);
        }
        *p = 0;
        l[UI_COLS] = 0;
        ui_text(&u, 1 + i - top, l);
      }
      ui_text(&u, 5, n > 4 ? "UP/DN MORE  EXIT BACK" : "EXIT BACK");
      if (n > 4) api->lcd_bar(5, (uint32_t)(top + 4), (uint32_t)n);
      ui_now(&u);
    }
    k = ui_key(&u);
    if (k == 'X' || k == 'M') break;
    if (k == 'U' && top > 0) top--;
    else if (k == 'D' && top + 4 < n) top++;
    else k = 0;
    api->delay_us(20000);
  }
}

int k6_main(const k6_api_t *api) {
  rt_flash_t fl;
  rt_t t;
  rt_rec_t r;
  char line[64], *p;
  int n, slot;
  if (api->level < 5) return -3;
  fl.read = fr, fl.write = fw, fl.erase = fe, fl.ctx = (void *)api;
  n = rt_mount(&t, &fl, RT_BASE, 0);
  p = put(line, "RT live="); p = dec(p, n < 0 ? 0 : t.live);
  p = put(p, " dead="); p = dec(p, t.dead);
  p = put(p, " free="); p = dec(p, t.free);
  p = put(p, " spare="); p = dec(p, t.spare);
  p = put(p, " repairs="); p = dec(p, t.repairs);
  p = put(p, " r="); p = dec(p, n);
  *p = 0;
  api->say(line);
  if (n < 0) return n;
  for (slot = rt_next(&t, -1, &r); slot >= 0; slot = rt_next(&t, slot, &r)) {
    p = put(line, "RT "); p = dec(p, r.id);
    *p++ = ' '; p = dec(p, (int32_t)r.rx_hz);
    *p++ = ' '; p = dec(p, r.tx_off);
    p = put(p, " T"); p = dec(p, r.tx_tone);
    *p++ = ' '; p = r.call[0] ? field(p, r.call, 10) : put(p, "-");
    *p++ = ' '; *p++ = r.score ? (char)r.score : '-';
    *p++ = ' '; p = dec(p, (int32_t)r.best_rssi - 200);
    if (r.name[0]) *p++ = ' ', p = field(p, r.name, 16);
    *p = 0;
    api->say(line);
  }
  if (n > 0) show(api, &t, n);
  return n;
}
