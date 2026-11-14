/* sted: the K6 station editor's logic (sted.h). SPDX-License-Identifier: MIT */
#include "sted.h"

/* the 50 standard CTCSS tones, 0.1 Hz */
static const uint16_t CTCSS[] = {
    670,  693,  719,  744,  770,  797,  825,  854,  885,  915,  948,  974,  1000, 1035, 1072, 1109, 1148,
    1188, 1230, 1273, 1318, 1365, 1413, 1462, 1500, 1567, 1598, 1622, 1655, 1679, 1713, 1738, 1773, 1799,
    1835, 1862, 1899, 1928, 1966, 1995, 2035, 2065, 2107, 2181, 2257, 2291, 2336, 2418, 2503, 2541};
#define NCT ((int)(sizeof CTCSS / sizeof CTCSS[0]))
/* the 104 standard DCS codes (octal, as rt.h keeps them: the code's 9 bits) */
static const uint16_t DCS[] = {
    023, 025, 026, 031, 032, 036, 043, 047, 051, 053, 054, 065, 071, 072, 073, 074, 0114, 0115, 0116,
    0122, 0125, 0131, 0132, 0134, 0143, 0145, 0152, 0155, 0156, 0162, 0165, 0172, 0174, 0205, 0212, 0223, 0225, 0226,
    0243, 0244, 0245, 0246, 0251, 0252, 0255, 0261, 0263, 0265, 0266, 0271, 0274, 0306, 0311, 0315, 0325, 0331, 0332,
    0343, 0346, 0351, 0356, 0364, 0365, 0371, 0411, 0412, 0413, 0423, 0431, 0432, 0445, 0446, 0452, 0454, 0455, 0462,
    0464, 0465, 0466, 0503, 0506, 0516, 0523, 0526, 0532, 0546, 0565, 0606, 0612, 0624, 0627, 0631, 0632, 0654, 0662,
    0664, 0703, 0712, 0723, 0731, 0732, 0734, 0743, 0754};
#define NDCS ((int)(sizeof DCS / sizeof DCS[0]))
/* a tone's place in the UP/DOWN cycle: none, the CTCSS tones, DCS normal, DCS inverted */
#define NTONE (1 + NCT + 2 * NDCS)
/* the offsets UP/DOWN steps through (Hz); * starts a custom one, typed in kHz */
static const int32_t OFFS[] = {0, 600000, -600000, 1600000, -1600000, 5000000, -5000000};
#define NOFF ((int)(sizeof OFFS / sizeof OFFS[0]))
/* multi-tap letters, key 0-9 (phone keypad) */
/* (arrays, not pointers: a module is built -fropi, and a table of pointers would need relocating) */
static const char TAP[10][6] = {" 0", "1/-.", "ABC2", "DEF3", "GHI4", "JKL5", "MNO6", "PQRS7", "TUV8", "WXYZ9"};
/* UP/DOWN on the last letter */
static const char CHARSET[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-.";
#define NCS ((int)(sizeof CHARSET - 1))
static const char LABEL[SF_N][5] = {"RX  ", "OFS ", "RXT ", "TXT ", "NAM ", "CAL ", "GRP ", "WIDE", "DMR "};

static void scp(void *d, const void *s, unsigned n) {
  uint8_t *a = (uint8_t *)d;
  const uint8_t *b = (const uint8_t *)s;
  while (n--) *a++ = *b++;
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

/* u's decimal digits into p (at least min of them, zero padded); returns the end. No divide. */
static char *digits(char *p, uint32_t u, int min) {
  static const uint32_t PW[] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
  int i, started = 0;
  for (i = 0; i < 10; i++) {
    int d = 0;
    while (u >= PW[i]) u -= PW[i], d++;
    if (d || started || 10 - i <= min) *p++ = (char)('0' + d), started = 1;
  }
  return p;
}

char *sted_mhz(char *p, uint32_t hz, int dec) {
  char t[12], *e = digits(t, hz, 7), *q = t;
  int n = (int)(e - t), i;
  for (i = 0; i < n - 6; i++) *p++ = *q++;
  *p++ = '.';
  for (i = 0; i < dec; i++) *p++ = *q++;
  return p;
}

char *sted_tone(char *p, uint16_t tone) {
  if (!tone) return put(p, "NONE");
  if (tone & RT_TONE_DCS) {
    *p++ = 'D';
    *p++ = (char)('0' + ((tone >> 6) & 7));
    *p++ = (char)('0' + ((tone >> 3) & 7));
    *p++ = (char)('0' + (tone & 7));
    *p++ = (tone & RT_TONE_DCS_INV) ? 'I' : 'N';
    return p;
  }
  {
    char t[12], *e = digits(t, tone, 2), *q = t;
    while (q < e - 1) *p++ = *q++;
    *p++ = '.';
    *p++ = *q;
  }
  return p;
}

/* a tone's place in the cycle (0 none; one not in the tables counts as none) */
static int tone_index(uint16_t tone) {
  int i;
  if (!tone) return 0;
  if (tone & RT_TONE_DCS) {
    for (i = 0; i < NDCS; i++)
      if (DCS[i] == (tone & 0x1FF)) return 1 + NCT + i + ((tone & RT_TONE_DCS_INV) ? NDCS : 0);
    return 0;
  }
  for (i = 0; i < NCT; i++)
    if (CTCSS[i] == tone) return 1 + i;
  return 0;
}

static uint16_t tone_at(int i) {
  if (i <= 0) return 0;
  if (i <= NCT) return CTCSS[i - 1];
  i -= 1 + NCT;
  if (i < NDCS) return (uint16_t)(RT_TONE_DCS | DCS[i]);
  return (uint16_t)(RT_TONE_DCS | RT_TONE_DCS_INV | DCS[i - NDCS]);
}

static int slen(const char *s, int max) {
  int n = 0;
  while (n < max && s[n]) n++;
  return n;
}

static int call_ok(char c) { return (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '/'; }

/* the text field's buffer and size */
static char *text_of(sted_t *e, int *max) {
  if (e->f == SF_NAME) {
    *max = (int)sizeof e->r.name;
    return e->r.name;
  }
  *max = (int)sizeof e->r.call;
  return e->r.call;
}

/* compare the fields the editor edits: rx, offset, tones, flags (wide, dmr), name, call, group */
int sted_changed(const sted_t *e) {
  const uint8_t *a = (const uint8_t *)&e->r, *b = (const uint8_t *)&e->was;
  int i;
  for (i = 4; i < 18; i++)
    if (a[i] != b[i]) return 1;
  for (i = 24; i < 50; i++)
    if (a[i] != b[i]) return 1;
  return e->r.group != e->was.group;
}

void sted_open(sted_t *e, const rt_rec_t *r) {
  uint8_t *z = (uint8_t *)e;
  unsigned i;
  for (i = 0; i < sizeof *e; i++) z[i] = 0;
  if (r) scp(&e->r, r, sizeof *r);
  scp(&e->was, &e->r, sizeof e->r);
  e->f = SF_RX;
}

static int same(const char *a, const char *b, int n) {
  while (n--)
    if (*a++ != *b++) return 0;
  return 1;
}

void sted_commit(sted_t *e) {
  rt_rec_t *r = &e->r;
  if (!same(r->name, e->was.name, sizeof r->name)) r->flags |= RT_F_NAME_USER;
  if (!same(r->call, e->was.call, sizeof r->call)) {
    r->flags |= RT_F_CALL_USER;
    r->call_flags &= (uint8_t) ~(RT_C_FROM_CW | RT_C_TWICE);
  }
  if (r->rx_tone != e->was.rx_tone || r->tx_tone != e->was.tx_tone) r->flags |= RT_F_TONE_USER;
  r->flags &= (uint16_t)~RT_F_AUTO;
}

void sted_saved(sted_t *e, const rt_rec_t *written) {
  if (written) scp(&e->r, written, sizeof e->r);
  scp(&e->was, &e->r, sizeof e->r);
}

/* the typed digits as a frequency: padded with zeros to 9 digits (Hz), so 442025 is 442.025 MHz */
static uint32_t typed_hz(const sted_t *e) {
  uint32_t v = 0;
  int i;
  for (i = 0; i < 9; i++) v = v * 10u + (uint32_t)(i < e->nd ? e->dig[i] - '0' : 0);
  return v;
}

/* the first CTCSS tone whose digits start with the ones typed */
static void tone_typed(sted_t *e, uint16_t *tone) {
  int i, j;
  for (i = 0; i < NCT; i++) {
    char t[12], *x = digits(t, CTCSS[i], 1);
    int n = (int)(x - t);
    if (n < e->nd) continue;
    for (j = 0; j < e->nd && t[j] == e->dig[j]; j++) {
    }
    if (j == e->nd) {
      *tone = CTCSS[i];
      return;
    }
  }
  e->msg = "NO SUCH TONE";
}

/* 4 on a tone: the other tone into it (Jeff 2026-11-14: TXT copies RXT and back). No CTCSS
   tone starts with a 4, so 4 as the first digit is free. */
static void tone_copy(sted_t *e) {
  rt_rec_t *r = &e->r;
  if (e->f == SF_TXT) r->tx_tone = r->rx_tone;
  else r->rx_tone = r->tx_tone;
  e->nd = 0;
}

static void open_field(sted_t *e) {
  if (e->f == SF_WIDE) {
    e->r.flags ^= RT_F_WIDE;
    return;
  }
  if (e->f == SF_DMR) {
    e->r.flags ^= RT_F_DMR;
    return;
  }
  scp(&e->fwas, &e->r, sizeof e->r);
  e->open = 1;
  e->nd = 0;
  e->tap_k = 0;
  e->fresh = 0;
  e->neg = e->r.tx_off < 0;
  e->cust = 0;
  if (e->f == SF_RX && e->r.rx_hz) {
    /* its digits, so * takes them off (Jeff 2026-11-14: * didn't delete): 442.025 MHz is
       "442025", the trailing zeros of its 9 digits (Hz) dropped down to 3 */
    char t[12], *x = digits(t, e->r.rx_hz, 9);
    int n = (int)(x - t), i;
    while (n > 3 && t[n - 1] == '0') n--;
    for (i = 0; i < n && i < (int)sizeof e->dig; i++) e->dig[i] = t[i];
    e->nd = (uint8_t)i;
    e->fresh = 1;
  }
}

/* # in an open field: keep it (RX checks its range first) */
static void close_field(sted_t *e) {
  if (e->f == SF_RX && e->nd) {
    uint32_t hz = typed_hz(e);
    if (hz < STED_LO_HZ || hz > STED_HI_HZ) {
      e->msg = "18-630 MHZ ONLY";
      return;
    }
    e->r.rx_hz = hz;
  }
  e->open = 0;
}

static int step(int i, int d, int n) {
  i += d;
  if (i < 0) i = n - 1;
  if (i >= n) i = 0;
  return i;
}

static void text_key(sted_t *e, int k, uint32_t ms) {
  int max, n, i;
  char *s = text_of(e, &max);
  n = slen(s, max);
  if (k >= '0' && k <= '9') {
    const char *L = TAP[k - '0'];
    int nl = slen(L, 8);
    if (e->tap_k == k && n && ms - e->tap_ms < STED_TAP_MS) {
      /* the same key again soon: the next letter it carries (a call skips what it can't hold) */
      i = e->tap_i;
      do i = step(i, 1, nl);
      while (e->f == SF_CALL && !call_ok(L[i]) && i != e->tap_i);
      e->tap_i = (uint8_t)i;
      s[n - 1] = L[i];
    } else {
      for (i = 0; i < nl && e->f == SF_CALL && !call_ok(L[i]); i++) {
      }
      if (i == nl || n >= max) {
        if (n >= max) e->msg = "FULL";
        e->tap_k = 0;
      } else {
        s[n] = L[i];
        e->tap_k = (uint8_t)k;
        e->tap_i = (uint8_t)i;
      }
    }
    e->tap_ms = ms;
    return;
  }
  e->tap_k = 0;
  if (k == '*') {
    if (n) s[n - 1] = 0;
  } else if ((k == 'U' || k == 'D') && n) {
    for (i = 0; i < NCS && CHARSET[i] != s[n - 1]; i++) {
    }
    do i = step(i, k == 'U' ? 1 : -1, NCS);
    while (e->f == SF_CALL && !call_ok(CHARSET[i]));
    s[n - 1] = CHARSET[i];
  }
}

static void field_key(sted_t *e, int k, uint32_t ms) {
  rt_rec_t *r = &e->r;
  int i;
  switch (e->f) {
  case SF_RX:
    if (k >= '0' && k <= '9' && e->fresh) e->nd = 0;   /* the first digit typed starts over */
    if (k >= '0' && k <= '9' && e->nd < 9) e->dig[e->nd++] = (char)k;
    else if (k == '*' && e->nd) e->nd--;
    e->fresh = 0;
    break;
  case SF_OFF:
    if ((k == 'U' || k == 'D') && !e->cust) {
      for (i = 0; i < NOFF && OFFS[i] != r->tx_off; i++) {
      }
      if (i == NOFF) i = 0;
      r->tx_off = OFFS[step(i, k == 'U' ? 1 : -1, NOFF)];
      e->neg = r->tx_off < 0;
    } else if (k == '*' && !e->cust) {
      /* a custom offset (Jeff 2026-11-14: * starts it), typed in kHz: 600, 5000, 7600 */
      e->cust = 1;
      e->nd = 0;
      r->tx_off = 0;
    } else if (e->cust && ((k >= '0' && k <= '9' && e->nd < 5) || (k == '*' && e->nd))) {
      int32_t khz = 0;
      if (k == '*') e->nd--;
      else e->dig[e->nd++] = (char)k;
      for (i = 0; i < e->nd; i++) khz = khz * 10 + (e->dig[i] - '0');
      r->tx_off = (e->neg ? -khz : khz) * 1000;
    } else if (e->cust && (k == '*' || k == 'U' || k == 'D')) {
      /* nothing typed yet: * (or UP/DOWN) picks the sign */
      e->neg = !e->neg;
    }
    break;
  case SF_RXT:
  case SF_TXT: {
    uint16_t *t = e->f == SF_RXT ? &r->rx_tone : &r->tx_tone;
    if (k == 'U' || k == 'D') {
      *t = tone_at(step(tone_index(*t), k == 'U' ? 1 : -1, NTONE));
      e->nd = 0;
    } else if (k == '4' && !e->nd) {
      tone_copy(e);
    } else if (k >= '0' && k <= '9' && e->nd < 4) {
      e->dig[e->nd++] = (char)k;
      tone_typed(e, t);
    } else if (k == '*' && e->nd) {
      /* the last digit typed off; the tone the rest pick */
      if (--e->nd) tone_typed(e, t);
    } else if (k == '*') {
      *t = 0;
    }
    break;
  }
  case SF_NAME:
  case SF_CALL:
    text_key(e, k, ms);
    break;
  case SF_GRP:
    if (k == 'U' || k == 'D') r->group = (uint8_t)((r->group & 0xF0) | step(r->group & 15, k == 'U' ? 1 : -1, 16));
    break;
  }
}

int sted_key(sted_t *e, int k, uint32_t ms) {
  int ask = e->ask;
  e->msg = 0;
  e->ask = 0;
  if (e->open) {
    if (k == '#') {
      close_field(e);
    } else if (k == 'X') {
      scp(&e->r, &e->fwas, sizeof e->r);
      e->open = 0;
    } else if (k == 'M') {
      e->open = 0;
      goto list;
    } else {
      field_key(e, k, ms);
    }
    return STED_NONE;
  }
list:
  switch (k) {
  case 'U':
    e->f = (uint8_t)step(e->f, -1, SF_N);
    break;
  case 'D':
    e->f = (uint8_t)step(e->f, 1, SF_N);
    break;
  case '#':
    open_field(e);
    break;
  case '4':
    if (e->f == SF_RXT || e->f == SF_TXT) tone_copy(e);
    break;
  case '5':
    if (!e->r.rx_hz) e->msg = "SET RX FIRST";
    else if (!sted_changed(e) && e->r.id) e->msg = "NO CHANGES";
    else return STED_SAVE;
    break;
  case 'X':
  case 'M':
    if (sted_changed(e) && ask != k) {
      e->ask = (uint8_t)k;
      break;
    }
    return k == 'X' ? STED_EXIT : STED_MENU;
  }
  return STED_NONE;
}

int sted_row(const sted_t *e, int f, char *out) {
  const rt_rec_t *r = &e->r;
  int open = e->open && e->f == f, max, n;
  char *p = put(out, LABEL[f]);
  *p++ = ' ';
  switch (f) {
  case SF_RX:
    if (open && e->nd) {
      int i;
      for (i = 0; i < e->nd; i++) {
        if (i == 3) *p++ = '.';
        *p++ = e->dig[i];
      }
    } else if (open || r->rx_hz) {
      if (r->rx_hz) p = sted_mhz(p, r->rx_hz, 5);
    } else {
      p = put(p, "---");
    }
    break;
  case SF_OFF:
    if (open && e->cust) {
      int i;
      *p++ = e->neg ? '-' : '+';
      for (i = 0; i < e->nd; i++) *p++ = e->dig[i];
    } else if (!r->tx_off) {
      p = put(p, "SIMPLEX");
    } else {
      *p++ = r->tx_off < 0 ? '-' : '+';
      p = sted_mhz(p, (uint32_t)(r->tx_off < 0 ? -r->tx_off : r->tx_off), 3);
    }
    break;
  case SF_RXT:
    p = sted_tone(p, r->rx_tone);
    break;
  case SF_TXT:
    p = sted_tone(p, r->tx_tone);
    break;
  case SF_NAME:
  case SF_CALL:
    max = f == SF_NAME ? (int)sizeof r->name : (int)sizeof r->call;
    {
      const char *s = f == SF_NAME ? r->name : r->call;
      n = slen(s, max);
      if (!n && !open) p = put(p, "-");
      while (n--) *p++ = *s++;
    }
    break;
  case SF_GRP:
    if (r->group & 15) *p++ = (char)('A' - 1 + (r->group & 15));
    else p = put(p, "ALL");
    break;
  case SF_WIDE:
    p = put(p, (r->flags & RT_F_WIDE) ? "YES" : "NO");
    break;
  case SF_DMR:
    p = put(p, (r->flags & RT_F_DMR) ? "YES" : "NO");
    break;
  }
  if (open && p - out < STED_ROW - 1) *p++ = '_';
  while (p - out > STED_ROW - 1) p--;
  *p = 0;
  return (int)(p - out);
}

int sted_hint(const sted_t *e, char *out) {
  char *p = out;
  if (e->msg) {
    p = put(p, e->msg);
  } else if (e->ask) {
    p = put(p, e->ask == 'X' ? "EXIT AGAIN: DISCARD" : "MENU AGAIN: DISCARD");
  } else if (!e->open) {
    p = put(p, (e->f == SF_WIDE || e->f == SF_DMR) ? "#-FLIP 5-SAVE EXIT"
               : e->f == SF_RXT ? "#-EDIT 4-COPY 5-SAVE"
               : e->f == SF_TXT ? "#-EDIT 4-COPY 5-SAVE"
                                : "#-EDIT 5-SAVE EXIT");
  } else if (e->f == SF_NAME || e->f == SF_CALL) {
    if (e->tap_k) {
      const char *L = TAP[e->tap_k - '0'];
      *p++ = (char)e->tap_k;
      *p++ = ':';
      while (*L) {
        if (e->f != SF_CALL || call_ok(*L)) *p++ = *L == ' ' ? '_' : *L;
        L++;
      }
      *p++ = ' ';
    }
    p = put(p, "*-DEL #-OK");
  } else if (e->f == SF_RX) {
    p = put(p, "DIGITS *-DEL #-OK");
  } else if (e->f == SF_RXT || e->f == SF_TXT) {
    p = put(p, e->nd ? "DIGITS *-DEL #-OK" : "4-COPY *-OFF #-OK");
  } else if (e->f == SF_OFF) {
    p = put(p, !e->cust ? "UP/DN *-CUSTOM #-OK" : e->nd ? "KHZ *-DEL #-OK" : "KHZ *-SIGN #-OK");
  } else {
    p = put(p, "UP/DN #-OK");
  }
  while (p - out > STED_ROW - 1) p--;
  *p = 0;
  return (int)(p - out);
}
