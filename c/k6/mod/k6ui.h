/* k6ui.h: a screen and keys for any K6 RAM module (Jeff 2026-11-14: "you load up a module and
   nothing changes, so it looks like something is broken").

   Every module in the release's library (tools/k6_release_modules.txt) shows on the screen what
   it is doing: its name and frequency on the title bar, what it is waiting for or hearing on
   line 1, the latest results on lines 2-4 (a log that scrolls up), and its keys on line 5. When
   it ends by itself (time up, done, refused) the last screen stays until a key, or UI_HOLD_MS,
   so a short run or an error can be read; a run stopped with a key goes straight back.

   The screen is module API level 6 (lcd_text / lcd_flush), the keys' identity this file's own
   scan of the keypad matrix (the API has only key_down / key_poll: any key). Both are used only
   when the image has level 6 or more, read at run time (ui_t.on), so a module built with this
   still runs on older images and in the emulator tests at level 2, as before.

   A flush draws the whole frame (20-40 ms on the radio): ui_flush takes a minimum gap so a
   module in a receive loop flushes a few times a second at most; ui_now flushes at once.
   The keypad is Aura's (src/device/keypad.rs at edf3b98, as surf.c and gw.c scan it): rows PB0,
   PB4, PB10, PB11, PB9 driven low one at a time, columns PC13, PC14, PF6, PB14; the side keys
   read with no row driven. Keys: '0'-'9', '*', '#', 'M' menu, 'X' exit, 'U' up, 'D' down,
   'A' side 1, 'B' side 2, 'N' band, 'V', 'W'.

   No libraries, no division, no writable statics. SPDX-License-Identifier: MIT */
#ifndef K6UI_H
#define K6UI_H
#include "../k6_mod.h"

#ifndef UI_HOLD_MS
#define UI_HOLD_MS 15000u          /* the end screen, unless a key comes first */
#endif
#define UI_COLS 21
#ifndef UI_LOG_N
#define UI_LOG_N 3                 /* the log's lines at most (lb - lt + 1) */
#endif
#define UI_GPIOB 0x48000400u
#define UI_GPIOC 0x48000800u
#define UI_GPIOF 0x48001400u
#define UI_ROWS_B ((1u << 0) | (1u << 4) | (1u << 9) | (1u << 10) | (1u << 11))

#define UI_FN static __attribute__((unused, noinline))

typedef struct {
  const k6_api_t *api;
  uint8_t on;                      /* the image has the module screen (level 6+) */
  uint8_t dirty;
  uint8_t lt, lb;                  /* the log's lines (2-4 by default) */
  uint8_t raw, key;                /* the keypad: the last read, the key held (debounced) */
  uint32_t t_fl, t_key, t_rep;
  char log[UI_LOG_N][UI_COLS + 1]; /* the log's lines */
} ui_t;

UI_FN char *ui_put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  *p = 0;
  return p;
}

/* n / d by shift and subtract (a module has no run-time library); a module that has one of
   its own defines UI_UDIV as its name */
#ifdef UI_UDIV
#define ui_udiv UI_UDIV
#else
UI_FN uint32_t ui_udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  if (!d) return 0;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
}
#endif

/* v in decimal, at least w digits */
UI_FN char *ui_dec(char *p, uint32_t v, int w) {
  char t[10];
  int n = 0;
  do {
    uint32_t q = ui_udiv(v, 10);
    t[n++] = (char)('0' + v - 10 * q);
    v = q;
  } while (v || n < w);
  while (n) *p++ = t[--n];
  *p = 0;
  return p;
}

UI_FN char *ui_sdec(char *p, int32_t v) {
  if (v < 0) *p++ = '-', v = -v;
  return ui_dec(p, (uint32_t)v, 1);
}

/* hz as MHz with 3 decimals ("144.174"), or 4 when the 100 Hz digit isn't 0 */
UI_FN char *ui_mhz(char *p, uint32_t hz) {
  uint32_t mhz = ui_udiv(hz, 1000000u), f = ui_udiv(hz - mhz * 1000000u, 100);
  p = ui_dec(p, mhz, 1);
  *p++ = '.';
  if (f - 10 * ui_udiv(f, 10)) return ui_dec(p, f, 4);
  return ui_dec(p, ui_udiv(f, 10), 3);
}

/* seconds as m:ss (or h:mm:ss) */
UI_FN char *ui_mmss(char *p, uint32_t s) {
  uint32_t m = ui_udiv(s, 60), h = ui_udiv(m, 60);
  if (h) p = ui_dec(p, h, 1), *p++ = ':', p = ui_dec(p, m - 60 * h, 2);
  else p = ui_dec(p, m, 1);
  *p++ = ':';
  return ui_dec(p, s - 60 * m, 2);
}

UI_FN void ui_text(ui_t *u, int line, const char *s) {
  if (!u->on) return;
  u->api->lcd_text(line, s);
  u->dirty = 1;
}

/* draw now if anything changed since the last flush and at least gap ms have passed */
UI_FN void ui_flush(ui_t *u, uint32_t gap) {
  uint32_t t;
  if (!u->on || !u->dirty) return;
  t = u->api->ms();
  if (t - u->t_fl < gap) return;
  u->api->lcd_flush();
  u->t_fl = u->api->ms();
  u->dirty = 0;
}

UI_FN void ui_now(ui_t *u) { ui_flush(u, 0); }

/* a log line (cut to the screen's width) at the bottom of lines lt..lb, the rest move up; a
   line the log hasn't reached yet keeps what it had ("LISTENING") */
UI_FN void ui_log(ui_t *u, const char *s) {
  int i, n = u->lb - u->lt;
  if (!u->on) return;
  for (i = 0; i < n; i++) {
    int c;
    for (c = 0; c <= UI_COLS; c++) u->log[i][c] = u->log[i + 1][c];
  }
  for (i = 0; i < UI_COLS && s[i]; i++) u->log[n][i] = s[i];
  u->log[n][i] = 0;
  for (i = 0; i <= n; i++)
    if (u->log[i][0]) ui_text(u, u->lt + i, u->log[i]); /* (a line not yet used keeps its text) */
}

/* the log's lines (and the screen's state for them) empty again */
UI_FN void ui_log_clear(ui_t *u) {
  int i;
  for (i = 0; i < UI_LOG_N; i++) u->log[i][0] = 0;
  for (i = u->lt; i <= u->lb; i++) ui_text(u, i, "");
}

UI_FN void ui_begin(ui_t *u, const k6_api_t *api, const char *title, const char *status) {
  int i;
  u->api = api;
  u->on = api->abi == 1 && api->level >= 6;
  u->dirty = 0, u->lt = 2, u->lb = 4;
  u->raw = u->key = 0;
  u->t_fl = u->t_key = u->t_rep = 0;
  for (i = 0; i < UI_LOG_N; i++) u->log[i][0] = 0;
  if (!u->on) return;
  for (i = 0; i < 6; i++) api->lcd_text(i, "");
  ui_text(u, 0, title);
  ui_text(u, 1, status);
  /* -1: no display (a test bench's image): no screen, and no end screen to wait on */
  if (api->lcd_flush() < 0) u->on = 0;
  u->t_fl = api->ms();
  u->dirty = 0;
}

/* ---- the keypad ---- */

static const char UI_KEYS[24] = {0,   'A', 0,   'B', '7', '8', '#', '9', '4', '5', '0', '6',
                                 '1', '2', '*', '3', 'M', 'U', 'X', 'D', 'V', 'W', 0,   'N'};
static const uint8_t UI_ROWPIN[6] = {0xFF, 0, 4, 10, 11, 9};

UI_FN uint32_t ui_rd(uint32_t a) { return *(volatile uint32_t *)a; }
UI_FN void ui_wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

/* the key held now (0: none); only on an image with the screen (key_poll: level 4) */
UI_FN int ui_scan(ui_t *u) {
  int ph, k = 0;
  if (!u->on || !u->api->key_poll()) return 0;
  for (ph = 0; ph < 6 && !k; ph++) {
    uint32_t c, col;
    ui_wr(UI_GPIOB + 0x18, UI_ROWS_B);
    if (ph) ui_wr(UI_GPIOB + 0x28, 1u << UI_ROWPIN[ph]);
    u->api->delay_us(5);
    c = ui_rd(UI_GPIOC + 0x10);
    if (!(c & (1u << 13))) col = 0;
    else if (!(c & (1u << 14))) col = 1;
    else if (!(ui_rd(UI_GPIOF + 0x10) & (1u << 6))) col = 2;
    else if (!(ui_rd(UI_GPIOB + 0x10) & (1u << 14))) col = 3;
    else continue;
    k = UI_KEYS[4 * ph + col];
  }
  ui_wr(UI_GPIOB + 0x18, UI_ROWS_B);
  return k;
}

/* a key press: the key once it reads the same twice at least 10 ms apart; UP and DOWN repeat.
   Call it often (every 10-100 ms); 0 most of the time. Without the screen: 0, always. */
UI_FN int ui_key(ui_t *u) {
  int k;
  uint32_t t;
  if (!u->on) return 0;
  t = u->api->ms();
  if (t - u->t_key < 10) return 0;
  u->t_key = t;
  k = ui_scan(u);
  if (k != u->raw) {
    u->raw = (uint8_t)k;
    return 0;
  }
  if (k != u->key) {
    u->key = (uint8_t)k;
    u->t_rep = t + 400;
    return k;
  }
  if (k && (k == 'U' || k == 'D') && (int32_t)(t - u->t_rep) >= 0) {
    u->t_rep = t + 150;
    return k;
  }
  return 0;
}

/* wait (up to 3 s) until no key is held */
UI_FN void ui_release(ui_t *u) {
  int i, n;
  if (!u->on) return;
  for (i = 0, n = 0; i < 300 && n < 3; i++) {
    n = u->api->key_poll() ? 0 : n + 1;
    u->api->delay_us(10000);
  }
  u->key = u->raw = 0;
}

/* the end: line 1 says how it went, line 5 how to leave; the screen stays until a key or
   UI_HOLD_MS. stopped: the run ended on a key (the user wants out): no wait */
UI_FN void ui_end(ui_t *u, const char *how, int stopped) {
  uint32_t t0;
  if (!u->on) return;
  if (how) ui_text(u, 1, how); /* (0: line 1 as it is) */
  ui_text(u, 5, stopped ? "" : "ANY KEY: BACK");
  ui_now(u);
  if (stopped) return;
  ui_release(u);
  t0 = u->api->ms();
  while (u->api->ms() - t0 < UI_HOLD_MS && !u->api->key_poll()) u->api->delay_us(20000);
}

/* "MOD STOPPED" style messages with a number: "<s><v>" into buf */
UI_FN const char *ui_sv(char *buf, const char *s, int32_t v) {
  ui_sdec(ui_put(buf, s), v);
  return buf;
}

#endif
