/* edtrial: the station editor's key trial (the release plan's step 2, Jeff 2026-11-13). The
   editor (c/k6/sted.c, the logic the core will use) on the radio as a RAM module, on the image
   already there, so its keys can be tried before they're frozen into an image. It reads the
   saved stations from the repeater table (level 5's fl_read) and WRITES NOTHING: Save shows
   "TRIAL: NOT WRITTEN", keeps the change in RAM for this run and says the record on the serial
   report.

   Screens: the title bar and the hint line in the module text screen's 5x7, the rows in the
   small 4x6 font (Jeff 2026-11-14, as EXPLORE's list and the mock-ups), drawn by the module
   into the screen's waterfall area (level 6's lcd_wf: 30 pixel rows from y = 21, so five rows
   of 6), the selected row inverted:

     STATIONS 12            the saved table by frequency
     103.5 W7ABC            the selected station's tones and call (5x7)
     442.025 +5.0 LYNDEN HILL   UP/DOWN pick, 8 edit, 0 new station, EXIT (or MENU) leave
     ...
     8EDIT 0NEW EXIT

     EDIT 442.025 #7 *      the editor (sted.h): UP/DOWN field, # open, 5 save, EXIT back
     NAM  LYNDEN HILL_      the selected field in the 5x7, as it is typed
     NAM  LYNDEN HILL       (the small rows)
     ...
     #EDIT 5SAVE EXIT

   Line 1 is filled because the rows can't start higher: the module screen's waterfall area
   begins at y = 21. The core's editor draws its own screen and has no such gap.

   Serial (K6 MOD say ...): "ed n=12" at the start, "ed k=5 f=4 o=1" after every key (the key,
   the field, open, the field's row), two lines for each trial Save ("ed save id=7 rx=442025000
   off=5000000 t=1035/1035", "ed save g=2 fl=0008 LYNDEN HILL/W7ABC"), "ed end saves=1".
   Keys: the module scans the keypad itself (as surf.c). Receive set-up untouched, no TX.
   Returns the trial saves, -3 on an image below level 6, -4 with too little free RAM.
   No libraries, no division, no writable statics. SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 6
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "../rt.c"
#include "../sted.c"

K6_MODULE_NAME("EDTRIAL");

#define NST 48                  /* stations kept (the table holds up to 960 slots; a trial needs few) */
#define LINES 5                 /* small rows on screen between the title and the hint */
#define SMALL_COLS 32           /* 4x6 characters a row */

#define GPIOB 0x48000400u
#define GPIOC 0x48000800u
#define GPIOF 0x48001400u
#define IDR 0x10u
#define BSRR 0x18u
#define BRR 0x28u
#define ROWS_B ((1u << 0) | (1u << 4) | (1u << 9) | (1u << 10) | (1u << 11))

/* Aura's KEY_TABLE (as surf.c): phase 0 side keys, then rows PB0, PB4, PB10, PB11, PB9 */
static const char KEYS[24] = {0,   'A', 0,   'B', '7', '8', '#', '9', '4', '5', '0', '6',
                              '1', '2', '*', '3', 'M', 'U', 'X', 'D', 'V', 'W', 0,   'N'};
static const uint8_t ROWPIN[6] = {0xFF, 0, 4, 10, 11, 9};

/* embedded-graphics' FONT_4X6 (Aura's small font, as EXPLORE draws it), ' ' to '~': six rows of
   4 pixels, two rows a byte, the high nibble first, bit 3 of a row leftmost */
static const uint8_t FONT4[95][3] = {
    {0x00, 0x00, 0x00}, {0x44, 0x40, 0x40}, {0xaa, 0x00, 0x00}, {0xaf, 0xaf, 0xa0}, {0x4e, 0xc2, 0xe4}, {0x82, 0x48, 0x20}, {0x4a, 0x4a, 0x50}, {0x44, 0x00, 0x00},
    {0x24, 0x44, 0x42}, {0x84, 0x44, 0x48}, {0xa4, 0xe4, 0xa0}, {0x44, 0xe4, 0x40}, {0x00, 0x00, 0x48}, {0x00, 0xe0, 0x00}, {0x00, 0x00, 0x40}, {0x22, 0x48, 0x80},
    {0x4a, 0xea, 0x40}, {0x4c, 0x44, 0xe0}, {0x4a, 0x24, 0xe0}, {0xe2, 0x42, 0xc0}, {0xaa, 0xe2, 0x20}, {0xe8, 0xc2, 0xc0}, {0x68, 0xca, 0x40}, {0xe2, 0x48, 0x80},
    {0x6a, 0x4a, 0xc0}, {0x4a, 0x62, 0xc0}, {0x04, 0x00, 0x40}, {0x04, 0x00, 0x48}, {0x24, 0x84, 0x20}, {0x0e, 0x0e, 0x00}, {0x84, 0x24, 0x80}, {0xc2, 0x40, 0x40},
    {0x6a, 0xa8, 0x60}, {0x4a, 0xea, 0xa0}, {0xca, 0xca, 0xc0}, {0x4a, 0x8a, 0x40}, {0xca, 0xaa, 0xc0}, {0xe8, 0xc8, 0xe0}, {0xe8, 0xc8, 0x80}, {0x68, 0xaa, 0x60},
    {0xaa, 0xea, 0xa0}, {0xe4, 0x44, 0xe0}, {0x22, 0x2a, 0x40}, {0xaa, 0xca, 0xa0}, {0x88, 0x88, 0xe0}, {0xae, 0xea, 0xa0}, {0x2a, 0xea, 0x80}, {0x4a, 0xaa, 0x40},
    {0xca, 0xc8, 0x80}, {0x4a, 0xaa, 0x42}, {0xca, 0xca, 0xa0}, {0x68, 0x42, 0xc0}, {0xe4, 0x44, 0x40}, {0xaa, 0xaa, 0xe0}, {0xaa, 0xae, 0x40}, {0xaa, 0xee, 0xa0},
    {0xaa, 0x4a, 0xa0}, {0xaa, 0x44, 0x40}, {0xe2, 0x48, 0xe0}, {0x64, 0x44, 0x60}, {0x88, 0x42, 0x20}, {0xc4, 0x44, 0xc0}, {0x4a, 0x00, 0x00}, {0x00, 0x00, 0x0e},
    {0x42, 0x00, 0x00}, {0x06, 0xaa, 0x60}, {0x8c, 0xaa, 0xc0}, {0x06, 0x88, 0x60}, {0x26, 0xaa, 0x60}, {0x04, 0xac, 0x60}, {0x24, 0xe4, 0x40}, {0x06, 0xa6, 0x2c},
    {0x8c, 0xaa, 0xa0}, {0x40, 0xc4, 0xe0}, {0x20, 0x22, 0x2c}, {0x8a, 0xca, 0xa0}, {0xc4, 0x44, 0xe0}, {0x0a, 0xea, 0xa0}, {0x0c, 0xaa, 0xa0}, {0x04, 0xaa, 0x40},
    {0x0c, 0xac, 0x88}, {0x06, 0xaa, 0x62}, {0x0a, 0xc8, 0x80}, {0x06, 0xc2, 0xc0}, {0x4e, 0x44, 0x20}, {0x0a, 0xaa, 0x60}, {0x0a, 0xaa, 0x40}, {0x0a, 0xae, 0xa0},
    {0x0a, 0x44, 0xa0}, {0x0a, 0xa6, 0x2c}, {0x0e, 0x24, 0xe0}, {0x24, 0xc4, 0x42}, {0x44, 0x44, 0x40}, {0x84, 0x64, 0x48}, {0x5a, 0x00, 0x00},
};

typedef struct {
  const k6_api_t *api;
  sted_t e;
  rt_rec_t st[NST];
  uint32_t now, t_key, t_rep;
  int n, sel, top, edit, saves, ed_slot;
  uint8_t raw, key;
  char ln[STED_ROW + 40];
  uint8_t wf[LINES * 6 * 16];   /* the small rows, 128 pixels a pixel row (lcd_wf) */
} ed_t;

static uint32_t kp_rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static int kp_scan(ed_t *s) {
  int ph, k = 0;
  for (ph = 0; ph < 6 && !k; ph++) {
    uint32_t c, col;
    wr(GPIOB + BSRR, ROWS_B);
    if (ph) wr(GPIOB + BRR, 1u << ROWPIN[ph]);
    s->api->delay_us(5);
    c = kp_rd(GPIOC + IDR);
    if (!(c & (1u << 13))) col = 0;
    else if (!(c & (1u << 14))) col = 1;
    else if (!(kp_rd(GPIOF + IDR) & (1u << 6))) col = 2;
    else if (!(kp_rd(GPIOB + IDR) & (1u << 14))) col = 3;
    else continue;
    k = KEYS[4 * ph + col];
  }
  wr(GPIOB + BSRR, ROWS_B);
  return k;
}

/* a key once it reads the same twice 10 ms apart; UP and DOWN repeat */
static int kp_event(ed_t *s) {
  int k;
  if (s->now - s->t_key < 10) return 0;
  s->t_key = s->now;
  k = kp_scan(s);
  if (k != s->raw) {
    s->raw = (uint8_t)k;
    return 0;
  }
  if (k != s->key) {
    s->key = (uint8_t)k;
    s->t_rep = s->now + 400;
    return k;
  }
  if ((k == 'U' || k == 'D') && (int32_t)(s->now - s->t_rep) >= 0) {
    s->t_rep = s->now + 150;
    return k;
  }
  return 0;
}

static char *num(char *p, uint32_t v) { return digits(p, v, 1); }

static char *hex4(char *p, uint32_t v) {
  int i;
  for (i = 12; i >= 0; i -= 4) {
    uint32_t d = (v >> i) & 15;
    *p++ = (char)(d < 10 ? '0' + d : 'a' + d - 10);
  }
  return p;
}

static void say(ed_t *s, char *end) {
  *end = 0;
  s->api->say(s->ln);
}

/* the saved stations, newest version of each entry, by frequency. Read only: no rt_mount (its
   repairs write). */
static void load(ed_t *s) {
  uint32_t a;
  rt_rec_t r;
  int i, j;
  for (a = RT_BASE; a < RT_BASE + RT_SLOTS * RT_REC; a += RT_REC) {
    uint16_t st;
    if (s->api->fl_read(a, &st, 2) < 0) break;
    if ((st & 0xFF) != RT_ST_VALID) continue;
    if (s->api->fl_read(a, &r, sizeof r) < 0 || rt_crc(&r) != r.crc) continue;
    for (i = 0; i < s->n && s->st[i].id != r.id; i++) {
    }
    if (i < s->n) {
      if ((int32_t)(r.wseq - s->st[i].wseq) > 0) scp(&s->st[i], &r, sizeof r);
      continue;
    }
    if (s->n == NST) continue;
    scp(&s->st[s->n++], &r, sizeof r);
  }
  for (i = 1; i < s->n; i++)                  /* by frequency */
    for (j = i; j > 0 && s->st[j - 1].rx_hz > s->st[j].rx_hz; j--) {
      scp(&r, &s->st[j], sizeof r);
      scp(&s->st[j], &s->st[j - 1], sizeof r);
      scp(&s->st[j - 1], &r, sizeof r);
    }
}

/* small row r: n characters of t in 4x6, inverted when selected */
static void small(ed_t *s, int r, const char *t, int n, int inv) {
  uint8_t *b = s->wf + r * 6 * 16;
  int i, y;
  for (i = 0; i < 6 * 16; i++) b[i] = 0;
  for (i = 0; i < n && i < SMALL_COLS; i++) {
    int c = (uint8_t)t[i];
    const uint8_t *g = FONT4[(c < ' ' || c > '~' ? '?' : c) - ' '];
    for (y = 0; y < 6; y++) {
      uint8_t row = (uint8_t)((y & 1) ? g[y >> 1] & 15 : g[y >> 1] >> 4);
      b[y * 16 + (i >> 1)] |= (uint8_t)((i & 1) ? row : row << 4);
    }
  }
  if (inv)
    for (i = 0; i < 6 * 16; i++) b[i] = (uint8_t)~b[i];
}

static void line(ed_t *s, int i, char *end) {
  while (end < s->ln + 21) *end++ = ' ';
  s->ln[21] = 0;
  s->api->lcd_text(i, s->ln);
}

static void draw_list(ed_t *s) {
  const k6_api_t *api = s->api;
  char *p;
  int i;
  p = put(s->ln, "STATIONS ");
  line(s, 0, num(p, (uint32_t)s->n));
  if (s->sel < s->top) s->top = s->sel;
  if (s->sel >= s->top + LINES) s->top = s->sel - LINES + 1;
  /* line 1, between the title and the rows (the waterfall area starts at y = 21 on this API, so
     it would stand empty; Jeff 2026-11-14): the selected station's tones and call */
  p = s->ln;
  if (s->sel < s->n) {
    const rt_rec_t *r = &s->st[s->sel];
    int j;
    if (r->rx_tone || r->tx_tone) {
      p = sted_tone(p, r->rx_tone);
      if (r->tx_tone != r->rx_tone) {
        *p++ = '/';
        p = sted_tone(p, r->tx_tone);
      }
      *p++ = ' ';
    }
    for (j = 0; j < 10 && r->call[j]; j++) *p++ = r->call[j];
  }
  line(s, 1, p);
  for (i = 0; i < LINES; i++) {
    int k = s->top + i;
    p = s->ln;
    if (k < s->n) {
      const rt_rec_t *r = &s->st[k];
      p = sted_mhz(p, r->rx_hz, 3);
      *p++ = ' ';
      if (!r->tx_off) {
        p = put(p, "  S ");
      } else {
        *p++ = r->tx_off < 0 ? '-' : '+';
        p = sted_mhz(p, (uint32_t)(r->tx_off < 0 ? -r->tx_off : r->tx_off), 1);
      }
      *p++ = ' ';
      if (r->name[0]) {
        int j;
        for (j = 0; j < 16 && r->name[j]; j++) *p++ = r->name[j];
      } else if (r->call[0]) {
        int j;
        for (j = 0; j < 10 && r->call[j]; j++) *p++ = r->call[j];
      } else if (r->rx_tone) {
        p = sted_tone(p, r->rx_tone);
      }
    } else if (k == 0) {
      p = put(p, "NO SAVED STATIONS");
    }
    small(s, i, s->ln, (int)(p - s->ln), k < s->n && k == s->sel);
  }
  line(s, 5, put(s->ln, "8EDIT 0NEW EXIT"));
  api->lcd_flush();
}

static void draw_edit(ed_t *s) {
  char *p;
  int i, top = s->e.f - 2;
  if (top < 0) top = 0;
  if (top > SF_N - LINES) top = SF_N - LINES;
  p = put(s->ln, "EDIT ");
  if (s->e.r.rx_hz) p = sted_mhz(p, s->e.r.rx_hz, 3);
  else p = put(p, "NEW");
  if (s->e.r.id) {
    p = put(p, " #");
    p = num(p, s->e.r.id);
  }
  if (sted_changed(&s->e)) p = put(p, " *");
  line(s, 0, p);
  /* line 1: the selected field in the big font, what is being typed easy to read */
  line(s, 1, s->ln + sted_row(&s->e, s->e.f, s->ln));
  for (i = 0; i < LINES; i++) {
    int f = top + i;
    small(s, i, s->ln, sted_row(&s->e, f, s->ln), f == s->e.f);
  }
  line(s, 5, s->ln + sted_hint(&s->e, s->ln));
  s->api->lcd_flush();
}

/* Save in the trial: the record as sted_commit leaves it, on the serial report; kept in RAM */
static void trial_save(ed_t *s) {
  rt_rec_t *r = &s->e.r;
  char *p;
  int i;
  sted_commit(&s->e);
  if (!r->id) {                               /* a new station: a RAM id past the table's */
    uint16_t m = 0;
    for (i = 0; i < s->n; i++)
      if (s->st[i].id > m) m = s->st[i].id;
    r->id = (uint16_t)(m + 1);
  }
  p = put(s->ln, "ed save id=");
  p = num(p, r->id);
  p = put(p, " rx=");
  p = num(p, r->rx_hz);
  p = put(p, " off=");
  if (r->tx_off < 0) *p++ = '-';
  p = num(p, (uint32_t)(r->tx_off < 0 ? -r->tx_off : r->tx_off));
  p = put(p, " t=");
  p = num(p, r->rx_tone);
  *p++ = '/';
  p = num(p, r->tx_tone);
  say(s, p);
  p = put(s->ln, "ed save g=");
  p = num(p, r->group);
  p = put(p, " fl=");
  p = hex4(p, r->flags);
  *p++ = ' ';
  for (i = 0; i < 16 && r->name[i]; i++) *p++ = r->name[i];
  *p++ = '/';
  for (i = 0; i < 10 && r->call[i]; i++) *p++ = r->call[i];
  say(s, p);
  if (s->ed_slot >= 0) scp(&s->st[s->ed_slot], r, sizeof *r);
  else if (s->n < NST) scp(&s->st[s->n++], r, sizeof *r), s->ed_slot = s->n - 1;
  sted_saved(&s->e, 0);
  s->e.msg = "TRIAL: NOT WRITTEN";
  s->saves++;
}

int k6_main(const k6_api_t *api) {
  ed_t *s;
  uint32_t room;
  uint8_t *z;
  unsigned i;
  int k;
  char *p;
  if (api->level < 6) return -3;
  z = (uint8_t *)api->scratch(&room);
  if (room < sizeof(ed_t)) return -4;
  for (i = 0; i < sizeof(ed_t); i++) z[i] = 0;
  s = (ed_t *)z;
  s->api = api;
  load(s);
  p = put(s->ln, "ed n=");
  say(s, num(p, (uint32_t)s->n));
  s->now = api->ms();
  api->lcd_wf(s->wf, LINES * 6, 0);   /* the small rows, drawn into s->wf from here on */
  draw_list(s);
  for (;;) {
    api->delay_us(2000);
    s->now = api->ms();
    k = kp_event(s);
    if (!k) continue;
    if (!s->edit) {
      if (k == 'X' || k == 'M') break;
      if (k == 'U' && s->n) s->sel = s->sel ? s->sel - 1 : s->n - 1;
      if (k == 'D' && s->n) s->sel = s->sel + 1 < s->n ? s->sel + 1 : 0;
      if ((k == '8' && s->n) || k == '0') {
        s->ed_slot = k == '8' ? s->sel : -1;
        sted_open(&s->e, k == '8' ? &s->st[s->sel] : 0);
        if (k == '0') sted_key(&s->e, '#', s->now);   /* a new station starts typing RX */
        s->edit = 1;
      }
    } else {
      int r = sted_key(&s->e, k, s->now);
      if (r == STED_SAVE) trial_save(s);
      if (r == STED_EXIT || r == STED_MENU) s->edit = 0;
      if (r == STED_MENU) break;
    }
    p = put(s->ln, "ed k=");
    *p++ = (char)k;
    p = put(p, " f=");
    p = num(p, s->e.f);
    p = put(p, " o=");
    p = num(p, s->e.open);
    if (s->edit) {
      *p++ = ' ';
      p += sted_row(&s->e, s->e.f, p);
    }
    say(s, p);
    if (s->edit) draw_edit(s);
    else draw_list(s);
  }
  p = put(s->ln, "ed end saves=");
  say(s, num(p, (uint32_t)s->saves));
  return s->saves;
}
