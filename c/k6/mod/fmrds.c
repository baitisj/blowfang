/* fmrds: what the broadcast stations say over RDS, and the time from their clocks
   (docs/k6-fm-chip.md, "RDS module"). Receive only.

   The K6's RDA5807M (bit-banged I2C: SCL PA4, SDA PA12, as fmprobe drives it) sweeps the band,
   then each station with FM_TRUE is heard for a few seconds through the shared RDS decoder
   (c/common/rds.h): its PI, programme type, flags and name. The stations that gave the most
   clean groups are then heard until their clock group (4A) arrives, once a minute, while their
   radio text comes in. Every clock group the decoder passes becomes an offset, UTC less the
   module's ms, from the minute it names plus the time the group takes to arrive (lat_ms: the
   group starts on the minute edge and is ready 87.6 ms later). Stations agreeing within agree_ms
   vote, a PI counting once (a translator repeats its parent's clock): the largest group wins,
   and with two or more stations in it (or one, with flags 2) the radio's clock is set (level 8).
   A single station's clock can be minutes out (run fmp1b: one station ran 2 min fast), which
   is why one doesn't count by default.

   Error levels come from verbose mode (REG 0x56 = 0xAEFF, with OPEN_MODE when the write needs
   it): with them a clock group must be clean in B-D; without, a clock group is unchecked and
   needs a second station even with flags 2.

   Lines (serial say lines; PAR below sets the numbers):
     a 10=1 11=1                         the bus: sequential 0x10 and random 0x11 acknowledge
     v 5804 aec0>aeff ok                 chip ID, verbose mode ("om" when OPEN_MODE was needed)
     w0 / w1 / f0 / f1 <chars>           the sweep as fmprobe says it ('0' + RSSI / 2; t FM_TRUE)
     n 104.300 q58 g45 ok40 pi=1234 pty=10 f=tm ps=KBBB 104   a station after its short listen:
                                         settled RSSI, groups, clean groups, PI, programme type,
                                         flags (t TP, a TA, m music), the name ('.' unconfirmed)
     t 104.300 mjd=61004 01:42 o=-16 e=000 at=123456    a clock group as it arrives: UTC, local
                                         offset in half hours, BLER B-D ("---" unchecked), and
                                         the module's ms when it was ready
     x0 104.300 <32 chars> / x1          the radio text ('.' for what didn't arrive twice)
     y 104.300 ecc=a0 gt=0A 1A 2A 4A     the extended country code and the group types heard
     c 01:43:12.345 v=2 w=600 d=2026-11-25 o=-16    the voted clock: UTC at this line, its bound,
                                         stations agreeing, date, the local offset they send
     d 104.300 +12                       each clock group's distance from the vote, ms
     k -1234 w=5000                      the radio's own clock against the vote before it was set
                                         (level 8, when it had one), and its bound
     E n=2 ms=312000                     the end
   Screen (level 6): the phase and frequency, the stations' names as they come, the clock.
   Returns the stations agreeing on the clock (0: no clock), or -2 a key, -3 an image below
   level 2, -4 no chip, -5 a chip ID that isn't 0x58xx, -6 no scratch, -11 a bad PAR block.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "k6ui.h"
#include "../../common/rds.h"
K6_MODULE_NAME("FM RDS");

#define GPIOA 0x48000000u
#define G_MODER 0x00u
#define G_OTYPER 0x04u
#define G_OSPEEDR 0x08u
#define G_PUPDR 0x0Cu
#define G_IDR 0x10u
#define G_ODR 0x14u
#define G_BSRR 0x18u
#define G_BRR 0x28u
#define SCL (1u << 4)
#define SDA (1u << 12)
#define M2 ((3u << 8) | (3u << 24))

#define A_SEQ 0x20u
#define A_RND 0x22u
#define R02_ON 0x806Du /* DHIZ, muted, CLK_MODE 26 MHz, RDS_EN, NEW_METHOD, ENABLE */
#define R02_RDS 0x0008u
#define R04 0x0400u
#define R05 0x8880u    /* INT_MODE, SEEKTH 8, LNAP, volume 0 */
#define R07 0x4202u
#define NSW 128
#define NST 16         /* stations kept */
#define NCT 12         /* clock groups kept */
#define DAY KM_DAY_MS

typedef struct {
  char magic[8];      /*  0 "FMRDS001" */
  uint16_t half_us;   /*  8 I2C half bit */
  uint16_t quick_ms;  /* 10 a sweep step's wait */
  uint16_t settle_ms; /* 12 a station's wait before its listen */
  uint16_t scan_ms;   /* 14 the short listen, each station */
  uint16_t clk_s;     /* 16 the clock listen at most, each station (a clock group a minute) */
  uint16_t sw_lo;     /* 18 sweep, 100 kHz channels above 76 MHz: 119 = 87.9 MHz */
  uint16_t sw_hi;     /* 20 319 = 107.9 MHz */
  uint16_t agree_ms;  /* 22 clocks this close agree */
  uint16_t w_ms;      /* 24 the bound handed to the radio's clock, over half the voters' spread */
  uint8_t sw_step;    /* 26 channels a step */
  uint8_t n_clk;      /* 27 stations for the clock */
  uint8_t lat_ms;     /* 28 a clock group's start to its arrival */
  uint8_t flags;      /* 29 1 don't set the radio's clock, 2 one station may set it, 4 no verbose */
  uint8_t min_ok;     /* 30 clean groups (or groups, without verbose) to be a clock candidate */
  uint8_t lead_s;     /* 31 0: hear each clock station from the start (its radio text comes in);
                         else, once a clock group has given the minute's phase, tune the next
                         station this many seconds before the next minute and idle till then */
} par_t;              /* 32 bytes */

static const volatile par_t PAR = {{'F', 'M', 'R', 'D', 'S', '0', '0', '1'},
                                   10, 150, 400, 5000, 65, 119, 319, 1500, 500, 2, 4, 88, 0, 8, 0};

typedef struct {
  uint16_t ch, pi, g, ok;
  uint8_t q, pty, fl, psok;
  char ps[8];
} st_t; /* 20 bytes */

typedef struct {
  uint32_t o;   /* UTC ms of day less the module's ms */
  uint32_t at;  /* the module's ms at its arrival */
  uint32_t mjd;
  uint16_t pi, ch;
  int8_t off;
  uint8_t cte;
} ct_t; /* 20 bytes */

typedef struct {
  par_t q;
  uint16_t sh5[9];
  uint8_t sw[NSW], swf[NSW];
  st_t st[NST];
  ct_t ct[NCT];
  rds_t r;
  ui_t u;
  char ln[72];
  uint8_t nst, nct;
} sc_t;

typedef struct {
  const k6_api_t *a;
  sc_t *s;
  uint32_t half;
  int ra, verbose;
} ctx_t;

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

/* ---- the bus (fmprobe's: open-drain on the MCU's pull-ups) ---- */
static void hb(ctx_t *c) { c->a->delay_us(c->half); }
static void scl(ctx_t *c, int h) {
  wr(GPIOA + (h ? G_BSRR : G_BRR), SCL);
  hb(c);
}
static void sda(ctx_t *c, int h) { wr(GPIOA + (h ? G_BSRR : G_BRR), SDA); }
static int sda_in(void) { return (rd(GPIOA + G_IDR) & SDA) != 0; }
static void i2c_start(ctx_t *c) {
  sda(c, 1);
  scl(c, 1);
  sda(c, 0);
  hb(c);
  scl(c, 0);
}
static void i2c_stop(ctx_t *c) {
  sda(c, 0);
  hb(c);
  scl(c, 1);
  sda(c, 1);
  hb(c);
}
static int i2c_wb(ctx_t *c, uint32_t b) {
  int i, ack;
  for (i = 7; i >= 0; i--) {
    sda(c, (b >> i) & 1);
    hb(c);
    scl(c, 1);
    scl(c, 0);
  }
  sda(c, 1);
  hb(c);
  scl(c, 1);
  ack = !sda_in();
  scl(c, 0);
  return ack;
}
static uint32_t i2c_rb(ctx_t *c, int ack) {
  uint32_t v = 0;
  int i;
  sda(c, 1);
  for (i = 0; i < 8; i++) {
    hb(c);
    scl(c, 1);
    v = v << 1 | (uint32_t)sda_in();
    scl(c, 0);
  }
  sda(c, !ack);
  hb(c);
  scl(c, 1);
  scl(c, 0);
  sda(c, 1);
  return v;
}
static int probe(ctx_t *c, uint32_t addr) {
  int ack;
  i2c_start(c);
  ack = i2c_wb(c, addr);
  i2c_stop(c);
  return ack;
}
static int32_t rreg(ctx_t *c, uint32_t reg) {
  uint32_t hi, lo;
  i2c_start(c);
  if (!i2c_wb(c, A_RND) || !i2c_wb(c, reg)) {
    i2c_stop(c);
    return -1;
  }
  i2c_stop(c);
  i2c_start(c);
  if (!i2c_wb(c, A_RND | 1)) {
    i2c_stop(c);
    return -1;
  }
  hi = i2c_rb(c, 1);
  lo = i2c_rb(c, 0);
  i2c_stop(c);
  return (int32_t)(hi << 8 | lo);
}
static void rwr(ctx_t *c, uint32_t reg, uint32_t v) {
  i2c_start(c);
  i2c_wb(c, A_RND);
  i2c_wb(c, reg);
  i2c_wb(c, v >> 8);
  i2c_wb(c, v & 0xFF);
  i2c_stop(c);
}
static void wreg(ctx_t *c, uint32_t reg, uint32_t v) {
  uint32_t i;
  c->s->sh5[reg - 2] = (uint16_t)v;
  if (c->ra) {
    rwr(c, reg, v);
    return;
  }
  i2c_start(c);
  i2c_wb(c, A_SEQ);
  for (i = 2; i <= reg; i++) {
    i2c_wb(c, c->s->sh5[i - 2] >> 8);
    i2c_wb(c, c->s->sh5[i - 2] & 0xFF);
  }
  i2c_stop(c);
}
static void seqrd(ctx_t *c, uint16_t *w, int n) {
  int i;
  i2c_start(c);
  i2c_wb(c, A_SEQ | 1);
  for (i = 0; i < n; i++) {
    uint32_t hi = i2c_rb(c, 1);
    w[i] = (uint16_t)(hi << 8 | i2c_rb(c, i < n - 1));
  }
  i2c_stop(c);
}

/* ---- text ---- */
static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}
static char *dec(char *p, uint32_t v, int w) { return ui_dec(p, v, w); }
static char *hex(char *p, uint32_t v, int digits) {
  int i;
  for (i = (digits - 1) * 4; i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}
static uint32_t ch_khz(uint32_t ch) { return 76000u + ch * 100u; }
static char *khz(char *p, uint32_t ch) { /* "104.300" */
  uint32_t k = ch_khz(ch), m = ui_udiv(k, 1000);
  p = dec(p, m, 1);
  *p++ = '.';
  return dec(p, k - m * 1000, 3);
}
static char *mhz1(char *p, uint32_t ch) { /* "104.3" for the screen */
  uint32_t k = ui_udiv(ch_khz(ch), 100), m = ui_udiv(k, 10);
  p = dec(p, m, 1);
  *p++ = '.';
  return dec(p, k - m * 10, 1);
}
static char *hms(char *p, uint32_t ms, int frac) {
  uint32_t s = ui_udiv(ms, 1000), m = ui_udiv(s, 60), h = ui_udiv(m, 60);
  p = dec(p, h, 2);
  *p++ = ':';
  p = dec(p, m - h * 60, 2);
  if (frac < 0) return p;
  *p++ = ':';
  p = dec(p, s - m * 60, 2);
  if (!frac) return p;
  *p++ = '.';
  return dec(p, ms - s * 1000, 3);
}
static char *pch(char *p, const char *t, int n) { /* printable, else '.' */
  int i;
  for (i = 0; i < n; i++) *p++ = t[i] >= 32 && t[i] < 127 ? t[i] : '.';
  return p;
}
static void say(ctx_t *c, char *p) {
  *p = 0;
  c->a->say(c->s->ln);
}
static int key(ctx_t *c) {
  return (c->a->level >= 4 ? c->a->key_poll() : c->a->key_down()) || km_power_switch_off();
}
static void status(ctx_t *c, const char *what, uint32_t ch, uint32_t n, uint32_t of) {
  char l[24], *p = put(l, what);
  if (!c->s->u.on) return;
  if (ch) p = mhz1(p, ch);
  if (of) *p++ = ' ', p = dec(p, n, 1), *p++ = '/', p = dec(p, of, 1);
  *p = 0;
  ui_text(&c->s->u, 1, l);
  ui_flush(&c->s->u, 250);
}

/* ---- the chip ---- */
static void tune(ctx_t *c, uint32_t ch) { wreg(c, 3, ch << 6 | 0x10u | 2u << 2); }
static uint32_t look(ctx_t *c, uint32_t ms) { /* RSSI, FM_TRUE in bit 7 */
  uint16_t w[2];
  c->a->delay_us(ms * 1000u);
  seqrd(c, w, 2);
  return (uint32_t)(w[1] >> 9) | (w[1] & 0x100u ? 0x80u : 0);
}
static char rc(uint32_t r) { return (char)('0' + (r > 127 ? 63 : r >> 1)); }
static void row(ctx_t *c, char t, const uint8_t *v, int n, int raw) {
  int i = 0, part = 0;
  while (i < n) {
    int j;
    char *p = c->s->ln;
    *p++ = t;
    *p++ = (char)('0' + part);
    *p++ = ' ';
    for (j = 0; j < 50 && i < n; j++, i++) *p++ = raw ? (char)v[i] : rc(v[i]);
    say(c, p);
    part++;
  }
}

/* the decoder on station st until ms run out, or (clock 1) its first clock group passes.
   Returns 1 a clock group, 0 none, -2 a key */
static int listen(ctx_t *c, st_t *st, uint32_t run_ms, int clock) {
  sc_t *s = c->s;
  rds_t *r = &s->r;
  uint16_t w[6];
  uint32_t t0, prev = 0, last = 0xFFFFFFFFu, i;
  int got = 0;
  rds_init(r);
  tune(c, st->ch);
  st->q = (uint8_t)(look(c, s->q.settle_ms) & 0x7F);
  wreg(c, 2, R02_ON & ~R02_RDS); /* restart the decoder */
  wreg(c, 2, R02_ON);
  t0 = c->a->ms();
  while (c->a->ms() - t0 < run_ms) {
    uint32_t e = 0, ev, now;
    if (key(c)) return -2;
    ui_flush(&s->u, 500);
    c->a->delay_us(5000);
    seqrd(c, w, 6);
    now = c->a->ms();
    if (!(w[0] >> 15) || prev) {
      prev = w[0] >> 15;
      continue;
    }
    prev = 1;
    if (c->verbose) {
      int32_t v = rreg(c, 0x10);
      e = (uint32_t)(w[1] & 15u) << 4 | (v >= 0 ? ((uint32_t)v >> 12) & 15u : 15u);
    }
    ev = rds_group(r, &w[2], e, c->verbose);
    if (c->a->level >= 7) c->a->led(!e);
    if ((ev & RDS_EV_CT) && clock) {
      uint32_t key3 = r->mjd << 11 | (uint32_t)r->hh << 6 | r->mm;
      char *p;
      if (key3 == last) continue; /* the same group again */
      last = key3;
      p = put(s->ln, "t ");
      p = khz(p, st->ch);
      p = dec(put(p, " mjd="), r->mjd, 1);
      *p++ = ' ';
      p = hms(p, ((uint32_t)r->hh * 60u + r->mm) * 60000u, -1);
      p = ui_sdec(put(p, " o="), r->off);
      p = put(p, " e=");
      for (i = 0; i < 3; i++)
        *p++ = r->cte == RDS_CTE_UNCHECKED ? '-' : (char)('0' + ((r->cte >> (4 - 2 * i)) & 3u));
      p = dec(put(p, " at="), now, 1);
      say(c, p);
      if (s->nct < NCT) {
        ct_t *k = &s->ct[s->nct++];
        uint32_t o = ((uint32_t)r->hh * 60u + r->mm) * 60000u + s->q.lat_ms + DAY - now;
        if (o >= DAY) o -= DAY; /* (now < DAY: a run is minutes) */
        k->o = o, k->at = now, k->mjd = r->mjd, k->pi = r->pi, k->ch = st->ch;
        k->off = r->off, k->cte = r->cte;
      }
      {
        char l[24];
        p = put(mhz1(l, st->ch), " CT ");
        p = hms(p, ((uint32_t)r->hh * 60u + r->mm) * 60000u, -1);
        *p = 0;
        ui_log(&s->u, l);
      }
      got = 1;
      break;
    }
    if ((ev & RDS_EV_PS) && !clock) {
      char l[24], *p = put(mhz1(l, st->ch), " ");
      p = pch(p, r->ps, 8);
      *p = 0;
      ui_log(&s->u, l);
    }
  }
  if (c->a->level >= 7) c->a->led(0);
  st->pi = r->pi, st->pty = r->pty, st->fl = r->fl;
  if (r->g > st->g || !clock) st->g = r->g, st->ok = c->verbose ? r->ok : r->g;
  if (r->psok >= st->psok) {
    st->psok = r->psok;
    for (i = 0; i < 8; i++) st->ps[i] = r->ps[i];
  }
  return got;
}

/* a station's name line */
static void name_line(ctx_t *c, st_t *st) {
  char *p = put(c->s->ln, "n ");
  uint32_t i;
  p = khz(p, st->ch);
  p = dec(put(p, " q"), st->q, 1);
  p = dec(put(p, " g"), st->g, 1);
  p = dec(put(p, " ok"), st->ok, 1);
  p = hex(put(p, " pi="), st->pi, 4);
  p = dec(put(p, " pty="), st->pty, 1);
  p = put(p, " f=");
  if (st->fl & RDS_TP) *p++ = 't';
  if (st->fl & RDS_TA) *p++ = 'a';
  if (st->fl & RDS_MS) *p++ = 'm';
  p = put(p, " ps=");
  for (i = 0; i < 8; i++) *p++ = (st->psok >> (i >> 1) & 1) && st->ps[i] >= 32 && st->ps[i] < 127 ? st->ps[i] : '.';
  say(c, p);
}

/* what the clock listen also heard: the text, the country code, the group types */
static void text_lines(ctx_t *c, st_t *st) {
  rds_t *r = &c->s->r;
  char *p;
  uint32_t i, k;
  if (r->rtok) {
    uint32_t n = r->rtend ? r->rtend - 1u : 64u;
    for (k = 0; k < 2 && k * 32 < n; k++) {
      p = put(c->s->ln, k ? "x1 " : "x0 ");
      p = khz(p, st->ch);
      *p++ = ' ';
      for (i = k * 32; i < k * 32 + 32 && i < n; i++) {
        uint32_t seg = r->rtab > 2 ? i >> 1 : i >> 2; /* 2B: two characters a segment */
        char ch = r->rt[i];
        *p++ = (r->rtok >> seg & 1) && ch >= 32 && ch < 127 ? ch : '.';
      }
      say(c, p);
    }
  }
  p = put(c->s->ln, "y ");
  p = khz(p, st->ch);
  p = hex(put(p, " ecc="), r->ecc, 2);
  p = put(p, " gt=");
  for (i = 0; i < 32 && p < c->s->ln + 60; i++)
    if (r->gt >> i & 1) p = dec(p, i >> 1, 1), *p++ = (char)('A' + (i & 1)), *p++ = ' ';
  say(c, p - 1);
}

/* a - b around the day, in -DAY/2 .. DAY/2 */
static int32_t dd(uint32_t a, uint32_t b) {
  int32_t d = (int32_t)(a - b);
  if (d > (int32_t)(DAY / 2)) d -= (int32_t)DAY;
  if (d < -(int32_t)(DAY / 2)) d += (int32_t)DAY;
  return d;
}

/* the vote: the clock groups within agree_ms of one of them, counting each PI once; the
   largest such group, then the narrowest. Returns the stations behind it */
static int vote(ctx_t *c) {
  sc_t *s = c->s;
  uint32_t i, j, k, bi = 0, bn = 0, bspread = 0, unchecked = 0;
  int32_t dlo = 0, dhi = 0;
  char *p;
  for (i = 0; i < s->nct; i++) {
    uint32_t n = 0, chk = 0;
    int32_t a = 0, b = 0;
    for (j = 0; j < s->nct; j++) {
      int32_t d = dd(s->ct[j].o, s->ct[i].o);
      if (d > (int32_t)s->q.agree_ms || d < -(int32_t)s->q.agree_ms) continue;
      for (k = 0; k < j; k++) /* a PI already counted in this group */
        if (s->ct[k].pi == s->ct[j].pi) {
          int32_t dk = dd(s->ct[k].o, s->ct[i].o);
          if (dk <= (int32_t)s->q.agree_ms && dk >= -(int32_t)s->q.agree_ms) break;
        }
      if (d < a) a = d;
      if (d > b) b = d;
      if (k < j) continue;
      n++;
      if (s->ct[j].cte != RDS_CTE_UNCHECKED) chk = 1;
    }
    if (n > bn || (n == bn && (uint32_t)(b - a) < bspread)) {
      bn = n, bi = i, bspread = (uint32_t)(b - a), dlo = a, dhi = b, unchecked = !chk;
    }
  }
  if (!bn) return 0;
  {
    uint32_t est = s->ct[bi].o + DAY + (uint32_t)((dlo + dhi) >> 1), now, utc, w, y, m, d, mjd, last = bi;
    if (est >= DAY) est -= DAY;
    for (j = 0; j < s->nct; j++) {
      int32_t e = dd(s->ct[j].o, est);
      if (e > (int32_t)s->q.agree_ms || e < -(int32_t)s->q.agree_ms) continue;
      if (s->ct[j].at > s->ct[last].at) last = j;
    }
    now = c->a->ms();
    utc = est + now;
    if (utc >= DAY) utc -= DAY;
    mjd = s->ct[last].mjd;
    /* the date: the last voter's day, one on if its minute plus the time since passed midnight */
    {
      uint32_t t = s->ct[last].o + s->ct[last].at;
      if (t >= DAY) t -= DAY;
      if (t + (now - s->ct[last].at) >= DAY) mjd++;
    }
    rds_date(mjd, &y, &m, &d);
    w = (bspread >> 1) + s->q.w_ms;
    p = hms(put(s->ln, "c "), utc, 1);
    p = dec(put(p, " v="), bn, 1);
    p = dec(put(p, " w="), w, 1);
    p = dec(put(p, " d="), y, 4);
    *p++ = '-';
    p = dec(p, m, 2);
    *p++ = '-';
    p = dec(p, d, 2);
    p = ui_sdec(put(p, " o="), s->ct[last].off);
    say(c, p);
    for (j = 0; j < s->nct; j++) {
      p = put(s->ln, "d ");
      p = khz(p, s->ct[j].ch);
      *p++ = ' ';
      {
        int32_t e = dd(s->ct[j].o, est);
        if (e >= 0) *p++ = '+';
        p = ui_sdec(p, e);
      }
      say(c, p);
    }
    if (c->a->level >= 8) {
      uint32_t rutc, rw;
      if (!c->a->clock_get(&rutc, 0, &rw)) {
        uint32_t t = c->a->ms();
        int32_t e;
        utc = est + t;
        if (utc >= DAY) utc -= DAY;
        e = dd(rutc, utc);
        p = put(s->ln, "k ");
        if (e >= 0) *p++ = '+';
        p = ui_sdec(p, e);
        p = dec(put(p, " w="), rw, 1);
        say(c, p);
      }
      if (!(s->q.flags & 1) && (bn >= 2 || ((s->q.flags & 2) && !unchecked))) {
        uint32_t t = c->a->ms();
        utc = est + t;
        if (utc >= DAY) utc -= DAY;
        c->a->clock_set(utc, KM_DATE(d, m, y), w);
      }
    }
    if (s->u.on) {
      char l[24];
      p = hms(put(l, "UTC "), utc, 0);
      p = dec(put(p, " V"), bn, 1);
      *p = 0;
      ui_text(&s->u, 1, l);
    }
  }
  return (int)bn;
}

int k6_main(const k6_api_t *api) {
  ctx_t cx, *c = &cx;
  sc_t *s;
  uint8_t *d;
  const volatile uint8_t *src = (const volatile uint8_t *)&PAR;
  uint32_t i, n, fl, bytes = 0, moder, otyper, ospeed, pupdr, odr, t0, nsw;
  int32_t id, v56 = -1;
  int ret = 0, stopped = 0;
  char *p;
  if (api->level < 2) return -3;
  s = (sc_t *)api->scratch(&bytes);
  if (!s || bytes < sizeof(sc_t)) return -6;
  d = (uint8_t *)&s->q;
  for (i = 0; i < sizeof s->q; i++) d[i] = src[i];
  if (s->q.half_us < 2 || s->q.sw_step < 1 || s->q.sw_hi <= s->q.sw_lo || s->q.sw_hi > 320 ||
      s->q.n_clk > NST)
    return -11;
  for (n = 0, i = s->q.sw_lo; i <= s->q.sw_hi; i += s->q.sw_step) {
    __asm__("" : "+r"(i));
    n++;
  }
  if (n > NSW) return -11;
  nsw = n;
  c->a = api, c->s = s, c->half = s->q.half_us, c->ra = 0, c->verbose = 0;
  s->nst = s->nct = 0;
  t0 = api->ms();
  ui_begin(&s->u, api, "FM RDS", "STARTING");
  ui_text(&s->u, 5, "ANY KEY: STOP");

  moder = rd(GPIOA + G_MODER), otyper = rd(GPIOA + G_OTYPER), ospeed = rd(GPIOA + G_OSPEEDR);
  pupdr = rd(GPIOA + G_PUPDR), odr = rd(GPIOA + G_ODR);
  wr(GPIOA + G_BSRR, SCL | SDA);
  wr(GPIOA + G_OTYPER, otyper | SCL | SDA);
  wr(GPIOA + G_PUPDR, (pupdr & ~M2) | (1u << 8) | (1u << 24));
  wr(GPIOA + G_OSPEEDR, ospeed | M2);
  wr(GPIOA + G_MODER, (moder & ~M2) | (1u << 8) | (1u << 24));
  hb(c);
  i2c_stop(c);

  {
    int a10 = probe(c, A_SEQ), a11 = probe(c, A_RND);
    p = put(s->ln, "a 10=");
    *p++ = (char)('0' + a10);
    p = put(p, " 11=");
    *p++ = (char)('0' + a11);
    say(c, p);
    if (!a10 && !a11) {
      ret = -4;
      goto out_pins;
    }
    id = a11 ? rreg(c, 0) : -1;
    c->ra = id >= 0 && (id & 0xFF00) == 0x5800;
    if (a11 && !c->ra) {
      p = hex(put(s->ln, "v "), (uint32_t)id & 0xFFFF, 4);
      say(c, p);
      ret = -5;
      goto out_pins;
    }
  }

  for (i = 0; i < 9; i++) s->sh5[i] = 0;
  s->sh5[2] = R04, s->sh5[3] = R05, s->sh5[5] = R07;
  wreg(c, 2, R02_ON | 2u);
  wreg(c, 2, R02_ON);
  wreg(c, 4, R04);
  wreg(c, 5, R05);
  wreg(c, 7, R07);
  api->delay_us(s->q.settle_ms * 1000u);

  /* verbose mode: the error level of every block */
  p = hex(put(s->ln, "v "), (uint32_t)id & 0xFFFF, 4);
  if (c->ra && !(s->q.flags & 4)) {
    int32_t b;
    v56 = rreg(c, 0x56);
    p = hex(put(p, " "), (uint32_t)v56 & 0xFFFF, 4);
    rwr(c, 0x56, ((uint32_t)v56 & 0xFF00u) | 0xFFu);
    b = rreg(c, 0x56);
    if ((b & 0xFF) != 0xFF) {
      wreg(c, 6, 0x6000u); /* OPEN_MODE 11 */
      rwr(c, 0x56, ((uint32_t)v56 & 0xFF00u) | 0xFFu);
      b = rreg(c, 0x56);
      p = put(p, " om");
    }
    c->verbose = (b & 0xFF) == 0xFF;
    *p++ = '>';
    p = hex(p, (uint32_t)b & 0xFFFF, 4);
    p = put(p, c->verbose ? " ok" : " no");
  }
  say(c, p);

  /* the sweep */
  status(c, "SWEEP", 0, 0, 0);
  for (n = 0, i = s->q.sw_lo; i <= s->q.sw_hi; i += s->q.sw_step, n++) {
    uint32_t r;
    if (key(c)) {
      ret = -2;
      goto out;
    }
    tune(c, i);
    r = look(c, s->q.quick_ms);
    s->sw[n] = (uint8_t)(r & 0x7F);
    s->swf[n] = (uint8_t)(r & 0x80 ? 't' : '.');
    if (s->u.on) api->lcd_bar(1, n + 1, nsw);
  }
  if (s->u.on) api->lcd_bar(1, 0, 0);
  row(c, 'w', s->sw, (int)nsw, 0);
  row(c, 'f', s->swf, (int)nsw, 1);

  /* the stations: FM_TRUE local maxima, strongest first */
  for (i = 0, fl = 127; i < nsw; i++)
    if (s->sw[i] < fl) fl = s->sw[i];
  for (i = 0; i < nsw; i++) {
    uint32_t r = s->sw[i], k, m;
    if (s->swf[i] != 't' || (i && s->sw[i - 1] > r) || (i + 1 < nsw && s->sw[i + 1] > r)) continue;
    if (i && s->sw[i - 1] == r && s->swf[i - 1] == 't') continue;
    for (k = 0; k < s->nst && s->st[k].q >= r; k++)
      ;
    if (k == NST) continue;
    if (s->nst < NST) s->nst++;
    for (m = s->nst - 1u; m > k; m--) { /* (byte copies: a module has no memcpy) */
      uint8_t *to = (uint8_t *)&s->st[m], *from = (uint8_t *)&s->st[m - 1];
      uint32_t b;
      for (b = 0; b < sizeof(st_t); b++) to[b] = from[b];
    }
    {
      st_t *st = &s->st[k];
      uint8_t *z = (uint8_t *)st;
      for (m = 0; m < sizeof *st; m++) z[m] = 0;
      st->ch = (uint16_t)(s->q.sw_lo + i * s->q.sw_step);
      st->q = (uint8_t)r;
    }
  }
  (void)fl;

  /* a short listen on each */
  for (n = 0; n < s->nst; n++) {
    status(c, "SCAN ", s->st[n].ch, n + 1, s->nst);
    if (listen(c, &s->st[n], s->q.scan_ms, 0) == -2) {
      ret = -2;
      goto out;
    }
    name_line(c, &s->st[n]);
  }

  /* the clock: the stations with the most clean groups, each until its clock group */
  {
    uint32_t done = 0, used = 0;
    while (done < s->q.n_clk) {
      uint32_t b = NST, bok = 0;
      int r;
      for (n = 0; n < s->nst; n++)
        if (!(used >> n & 1) && s->st[n].ok >= s->q.min_ok && s->st[n].ok > bok) b = n, bok = s->st[n].ok;
      if (b == NST) break;
      used |= 1u << b;
      if (s->q.lead_s && s->nct) { /* idle until lead_s before the next minute */
        ct_t *k = &s->ct[s->nct - 1];
        uint32_t now = api->ms(), x = k->o + now, edge;
        if (x >= DAY) x -= DAY;
        x -= 60000u * ui_udiv(x, 60000u);
        edge = now + 60000u - x;
        status(c, "WAIT ", s->st[b].ch, done + 1, s->q.n_clk);
        while ((int32_t)(edge - s->q.lead_s * 1000u - api->ms()) > 0) {
          if (key(c)) {
            ret = -2;
            goto out;
          }
          ui_flush(&s->u, 500);
          api->delay_us(50000);
        }
      }
      status(c, "CLOCK ", s->st[b].ch, done + 1, s->q.n_clk);
      r = listen(c, &s->st[b], s->q.clk_s * 1000u, 1);
      if (r == -2) {
        ret = -2;
        goto out;
      }
      text_lines(c, &s->st[b]);
      done++;
    }
  }
  ret = vote(c);

out:
  stopped = ret == -2;
  if (v56 >= 0) rwr(c, 0x56, (uint32_t)v56);
  if (s->sh5[4]) wreg(c, 6, 0);
  wreg(c, 2, R02_ON & ~(1u | R02_RDS));
out_pins:
  i2c_stop(c);
  wr(GPIOA + G_MODER, (rd(GPIOA + G_MODER) & ~M2) | (moder & M2));
  wr(GPIOA + G_OTYPER, (rd(GPIOA + G_OTYPER) & ~(SCL | SDA)) | (otyper & (SCL | SDA)));
  wr(GPIOA + G_OSPEEDR, (rd(GPIOA + G_OSPEEDR) & ~M2) | (ospeed & M2));
  wr(GPIOA + G_PUPDR, (rd(GPIOA + G_PUPDR) & ~M2) | (pupdr & M2));
  wr(GPIOA + G_BSRR, (odr & SCL ? SCL : SCL << 16) | (odr & SDA ? SDA : SDA << 16));
  p = ui_sdec(put(s->ln, "E n="), ret);
  p = dec(put(p, " ms="), api->ms() - t0, 1);
  say(c, p);
  ui_end(&s->u, ret > 0 ? 0 : ret == 0 ? "NO CLOCK HEARD" : stopped ? "STOPPED" : "NO FM CHIP", stopped);
  return ret;
}
