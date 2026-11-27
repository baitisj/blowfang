/* fmrds: the RDS module on the UV-21 Pro (the K6's c/k6/mod/fmrds.c, docs/k6-fm-chip.md "The RDS
   module"), as a cable module: 3.9 KB of code plus its RAM, too big for the old 4 KB block, so
   it needs the 12 KB arena (PR #293). Receive only, serial lines only (no screen, no radio clock
   on the port yet), the K6 module's line tags so tools/k6_fmrds.py report reads both.

   The chip: the RDA5807-family part at 7-bit 0x30 on SCL PA4 / SDA PB6 (SDA shared with the
   BK4829's 3-wire bus, whose SCN stays high), set up the stock way with the audio muted, driven
   open-drain on the pull-ups as radios/uv21/mod/fmprobe.c does (port 21 or later: it answers
   from boot). The decoder is the shared c/common/rds.h.

   Why RDS may work here when fmprobe heard no group (runs ufmp24-26): the stock REG 05H leaves
   INT_MODE (bit 15) at 0, where the chip holds RDSR for 5 ms only, and a poll (a 5 ms wait plus
   a ~4 ms read) misses it most times; the K6, which hears the full 11.4 groups a second, runs
   INT_MODE 1 (RDSR holds until the blocks are read). FR_INTMODE sets it. And the z line counts
   the decoder's sync flag (RDSS) on every poll, group or not, so a decoder that syncs but whose
   groups go unseen shows.

   Lines:
     v 5804 308d>308d no                chip ID, REG 56H before and after the verbose write
     w0 / w1 / f0 / f1 <chars>          the sweep ('0' + RSSI / 2; 't' FM_TRUE)
     n 104.300 q58 g45 ok0 pi=1234 pty=10 f=tm ps=KBBB 104   a station after its short listen
     z 104.300 polls=420 rdss=380 rdsr=45   polls, polls with RDSS set, RDSR rising edges
     t 104.300 mjd=61004 01:42 o=-16 e=000 at=123456   a clock group (e "---": unchecked)
     x0 / x1 104.300 <text>            its radio text
     y 104.300 ecc=a0 gt=0A 2A 4A      country code, group types
     c 01:43:12.345 v=2 w=600 d=2026-11-25 o=-16   the voted clock (two or more stations agreeing)
     d 104.300 +12                     each clock group's distance from the vote, ms
     E n=2 ms=150000                   the end: the stations agreeing, or the stations with a
                                       group when no clock came (as -100 - n), -4 no chip,
                                       -5 it reads 0xFF, -6 too little RAM
   The bench's uv21.mod step waits 180 s for the end: the defaults (a 15 s sweep, 3 s on each of
   up to 6 stations, then two clock stations, each until its clock group) fit.

   Settings (-D): FR_INTMODE (1), FR_HIDDEN stock hidden registers (1), FR_NEWM (1), FR_SCAN_MS
   (3000), FR_CLK_S (61), FR_NCLK (2), FR_NST (6), FR_QUICK_MS (150), FR_SETTLE_MS (300),
   FR_AGREE_MS (1500), FR_LAT_MS (88), FR_SW_LO / FR_SW_HI / FR_SW_STEP (119 / 319 / 2).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#include "uv21_mod.h"
#include "../../../c/common/rds.h"

#ifndef FR_INTMODE
#define FR_INTMODE 1
#endif
#ifndef FR_HIDDEN
#define FR_HIDDEN 1
#endif
#ifndef FR_NEWM
#define FR_NEWM 1
#endif
#ifndef FR_SCAN_MS
#define FR_SCAN_MS 3000
#endif
#ifndef FR_CLK_S
#define FR_CLK_S 61
#endif
#ifndef FR_NCLK
#define FR_NCLK 2
#endif
#ifndef FR_NST
#define FR_NST 6
#endif
#ifndef FR_QUICK_MS
#define FR_QUICK_MS 150
#endif
#ifndef FR_SETTLE_MS
#define FR_SETTLE_MS 300
#endif
#ifndef FR_AGREE_MS
#define FR_AGREE_MS 1500
#endif
#ifndef FR_LAT_MS
#define FR_LAT_MS 88
#endif
#ifndef FR_SW_LO
#define FR_SW_LO 119
#endif
#ifndef FR_SW_HI
#define FR_SW_HI 319
#endif
#ifndef FR_SW_STEP
#define FR_SW_STEP 2
#endif
#ifndef FR_PART /* 1 the stations (sweep, short listens); 2 the clock on the channels in FR_CH */
#define FR_PART 1
#endif
#ifndef FR_CH   /* part 2's stations, 100 kHz channels above 76 MHz (223 = 98.3 MHz) */
#define FR_CH 0
#endif
#define NSW ((FR_SW_HI - FR_SW_LO) / FR_SW_STEP + 1)
#define NCT 6
#define DAY 86400000u
#if NSW > 128 || FR_NST > 8 || FR_NCLK > FR_NST || FR_SW_HI > 320
#error "FR_ settings out of range"
#endif

#define GPIOA 0x48000000u
#define GPIOB 0x48000400u
#define G_CFGR 0x00u
#define G_OMODE 0x04u
#define G_PULL 0x0Cu
#define G_IDT 0x10u
#define G_ODT 0x14u
#define G_SCR 0x18u
#define G_CLR 0x28u
#define SCL (1u << 4)
#define SDA (1u << 6)
#define MSCL (3u << 8)
#define MSDA (3u << 12)
#define A_RND (0x30u << 1)

#define R02_ON (0x8069u | (FR_NEWM ? 4u : 0)) /* DHIZ, muted, 26 MHz, RDS_EN, (NEW_METHOD), ENABLE */
#define R02_RDS 0x0008u
#define R05 (0x00E0u | (FR_INTMODE ? 0x8000u : 0)) /* stock (both LNA inputs, volume 0) + INT_MODE */
#define R07 0x0202u

typedef struct {
  uint16_t ch, pi, g, ok;
  uint8_t q, pty, fl, psok;
  char ps[8];
} st_t;

typedef struct {
  uint32_t o, at, mjd;
  uint16_t pi, ch;
  int8_t off;
  uint8_t cte;
} ct_t;

typedef struct {
#if FR_PART == 1
  uint8_t sw[NSW], swf[NSW];
#else
  ct_t ct[NCT];
#endif
  st_t st[FR_NST];
  rds_t r;
  char ln[64];
  uint8_t nst, nct;
} sc_t;

typedef struct {
  const um_api_t *a;
  sc_t *s;
  int verbose;
} ctx_t;

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

/* ---- the bus (fmprobe's) ---- */
static void hb(ctx_t *c) { c->a->delay_us(10); }
static void scl(ctx_t *c, int h) {
  wr(GPIOA + (h ? G_SCR : G_CLR), SCL);
  hb(c);
}
static void sda(int h) { wr(GPIOB + (h ? G_SCR : G_CLR), SDA); }
static int sda_in(void) { return (rd(GPIOB + G_IDT) & SDA) != 0; }
static void i2c_start(ctx_t *c) {
  sda(1);
  scl(c, 1);
  sda(0);
  hb(c);
  scl(c, 0);
}
static void i2c_stop(ctx_t *c) {
  sda(0);
  hb(c);
  scl(c, 1);
  sda(1);
  hb(c);
}
static int i2c_wb(ctx_t *c, uint32_t b) {
  int i, ack;
  for (i = 7; i >= 0; i--) {
    sda((b >> i) & 1);
    hb(c);
    scl(c, 1);
    scl(c, 0);
  }
  sda(1);
  hb(c);
  scl(c, 1);
  ack = !sda_in();
  scl(c, 0);
  return ack;
}
static uint32_t i2c_rb(ctx_t *c, int ack) {
  uint32_t v = 0;
  int i;
  sda(1);
  for (i = 0; i < 8; i++) {
    hb(c);
    scl(c, 1);
    v = v << 1 | (uint32_t)sda_in();
    scl(c, 0);
  }
  sda(!ack);
  hb(c);
  scl(c, 1);
  scl(c, 0);
  sda(1);
  return v;
}
/* n registers from reg on: the register number, a stop, then a read that runs on (stock's) */
static int rdn(ctx_t *c, uint32_t reg, uint16_t *w, int n) {
  int i;
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
  for (i = 0; i < n; i++) {
    uint32_t hi = i2c_rb(c, 1);
    w[i] = (uint16_t)(hi << 8 | i2c_rb(c, i < n - 1));
  }
  i2c_stop(c);
  return 0;
}
static void wreg(ctx_t *c, uint32_t reg, uint32_t v) {
  i2c_start(c);
  i2c_wb(c, A_RND);
  i2c_wb(c, reg);
  i2c_wb(c, v >> 8);
  i2c_wb(c, v & 0xFF);
  i2c_stop(c);
}

/* ---- text ---- */
static char *put(char *p, const char *s) { return um_put(p, s); }
static char *dec(char *p, int32_t v) { return um_int(p, v); }
static char *d2(char *p, uint32_t v) {
  *p++ = (char)('0' + v / 10u % 10u);
  *p++ = (char)('0' + v % 10u);
  return p;
}
static char *khz(char *p, uint32_t ch) {
  uint32_t k = 76000u + ch * 100u;
  p = dec(p, (int32_t)(k / 1000u));
  *p++ = '.';
  *p++ = (char)('0' + k / 100u % 10u);
  return d2(p, k % 100u);
}
__attribute__((unused)) static char *hm(char *p, uint32_t hh, uint32_t mm) {
  p = d2(p, hh);
  *p++ = ':';
  return d2(p, mm);
}
static void say(ctx_t *c, char *p) {
  *p = 0;
  c->a->say(c->s->ln);
}
static char *tag(ctx_t *c, const char *t, uint32_t ch) { return khz(put(c->s->ln, t), ch); }
__attribute__((unused)) static int32_t dd(uint32_t a, uint32_t b) {
  int32_t d = (int32_t)(a - b);
  if (d > (int32_t)(DAY / 2)) d -= (int32_t)DAY;
  if (d < -(int32_t)(DAY / 2)) d += (int32_t)DAY;
  return d;
}

/* ---- the chip ---- */
static void tune(ctx_t *c, uint32_t ch) { wreg(c, 3, ch << 6 | 0x10u | 2u << 2); }
static uint32_t look(ctx_t *c, uint32_t ms) {
  uint16_t w[2] = {0, 0};
  um_wait_us(c->a, ms * 1000u);
  rdn(c, 0x0A, w, 2);
  return (uint32_t)(w[1] >> 9) | (w[1] & 0x100u ? 0x80u : 0);
}
#if FR_PART == 1
static void row(ctx_t *c, char t, const uint8_t *v, int raw) {
  int i = 0, part = 0;
  while (i < NSW) {
    int j;
    char *p = c->s->ln;
    *p++ = t;
    *p++ = (char)('0' + part++);
    *p++ = ' ';
    for (j = 0; j < 50 && i < NSW; j++, i++) *p++ = raw ? (char)v[i] : (char)('0' + (v[i] >> 1));
    say(c, p);
  }
}
#endif

/* the decoder on a station for ms, or (clock) until its first clock group passes */
static void listen(ctx_t *c, st_t *st, uint32_t ms, int clock) {
  sc_t *s = c->s;
  rds_t *r = &s->r;
  uint16_t w[6];
  uint32_t t0, prev = 0, polls = 0, sync = 0, edges = 0, i;
  char *p;
  rds_init(r);
  tune(c, st->ch);
  st->q = (uint8_t)(look(c, FR_SETTLE_MS) & 0x7F);
  wreg(c, 2, R02_ON & ~R02_RDS);
  wreg(c, 2, R02_ON);
  t0 = c->a->ms();
  while (c->a->ms() - t0 < ms) {
    uint32_t e = 0, now;
    c->a->delay_us(5000);
    if (rdn(c, 0x0A, w, 6)) continue;
    now = c->a->ms();
    polls++;
    if (w[0] & 0x1000u) sync++;
    if (!(w[0] >> 15) || prev) {
      prev = w[0] >> 15;
      continue;
    }
    prev = 1;
    edges++;
    if (c->verbose) {
      uint16_t v = 0xFFFF;
      rdn(c, 0x10, &v, 1);
      e = (uint32_t)(w[1] & 15u) << 4 | ((uint32_t)v >> 12);
    }
#if FR_PART == 1
    rds_group(r, &w[2], e, c->verbose);
    (void)now;
#else
    /* part 2 reads only the clock (4A): the range checks rds.h makes, B-D clean when the chip
       gives error levels; the vote is the report's (tools/k6_fmrds.py) */
    if ((w[3] >> 11) == 8 && (!c->verbose || !(e & 0x3Fu))) {
      uint32_t mjd = (uint32_t)(w[3] & 3u) << 15 | w[4] >> 1, hh = (uint32_t)(w[4] & 1u) << 4 | w[5] >> 12;
      uint32_t mm = (w[5] >> 6) & 63u, off = w[5] & 31u;
      if (hh < 24 && mm < 60 && off <= 28 && mjd >= RDS_MJD_LO && mjd < RDS_MJD_HI) {
        p = dec(put(tag(c, "t ", st->ch), " mjd="), (int32_t)mjd);
        p = hm(put(p, " "), hh, mm);
        p = dec(put(p, w[5] & 32u ? " o=-" : " o="), (int32_t)off);
        p = put(p, " e=");
        for (i = 0; i < 3; i++) *p++ = c->verbose ? (char)('0' + ((e >> (4 - 2 * i)) & 3u)) : '-';
        say(c, dec(put(p, " at="), (int32_t)now));
        if (clock) break;
      }
    }
#endif
  }
#if FR_PART == 1
  {
    st->pi = r->pi, st->pty = r->pty, st->fl = r->fl, st->g = r->g, st->ok = c->verbose ? r->ok : r->g;
    st->psok = r->psok;
    for (i = 0; i < 8; i++) st->ps[i] = r->ps[i];
  }
#endif
  p = dec(put(tag(c, "z ", st->ch), " polls="), (int32_t)polls);
  p = dec(put(p, " rdss="), (int32_t)sync);
  say(c, dec(put(p, " rdsr="), (int32_t)edges));
}

#if FR_PART == 1
static void name_line(ctx_t *c, st_t *st) {
  char *p = tag(c, "n ", st->ch);
  uint32_t i;
  p = dec(put(p, " q"), st->q);
  p = dec(put(p, " g"), st->g);
  p = dec(put(p, " ok"), st->ok);
  p = um_hex(put(p, " pi="), st->pi, 4);
  p = dec(put(p, " pty="), st->pty);
  p = put(p, " f=");
  if (st->fl & RDS_TP) *p++ = 't';
  if (st->fl & RDS_TA) *p++ = 'a';
  if (st->fl & RDS_MS) *p++ = 'm';
  p = put(p, " ps=");
  for (i = 0; i < 8; i++) *p++ = (st->psok >> (i >> 1) & 1) && st->ps[i] >= 32 && st->ps[i] < 127 ? st->ps[i] : '.';
  say(c, p);
}

#endif

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  ctx_t cx, *c = &cx;
  sc_t *s = (sc_t *)ram;
  uint32_t i, n, cfa, cfb, oma, omb, pua, pub, oda, odb, t0;
  uint16_t w[2] = {0xFFFF, 0xFFFF}, id = 0xFFFF, v56 = 0xFFFF, b56 = 0xFFFF;
  int ret;
  char *p;
  if (ram_len < sizeof(sc_t)) return -6;
  c->a = api, c->s = s, c->verbose = 0;
  t0 = api->ms();

  cfa = rd(GPIOA + G_CFGR), oma = rd(GPIOA + G_OMODE), pua = rd(GPIOA + G_PULL), oda = rd(GPIOA + G_ODT);
  cfb = rd(GPIOB + G_CFGR), omb = rd(GPIOB + G_OMODE), pub = rd(GPIOB + G_PULL), odb = rd(GPIOB + G_ODT);
  wr(GPIOA + G_SCR, SCL);
  wr(GPIOB + G_SCR, SDA);
  wr(GPIOA + G_OMODE, oma | SCL);
  wr(GPIOB + G_OMODE, omb | SDA);
  wr(GPIOA + G_PULL, (pua & ~MSCL) | (1u << 8));
  wr(GPIOB + G_PULL, (pub & ~MSDA) | (1u << 12));
  wr(GPIOA + G_CFGR, (cfa & ~MSCL) | (1u << 8));
  wr(GPIOB + G_CFGR, (cfb & ~MSDA) | (1u << 12));
  hb(c);
  i2c_stop(c);

  /* the stock setup, muted */
  wreg(c, 2, 2u);
  api->delay_us(10000);
  wreg(c, 2, R02_ON);
  api->delay_us(10000);
  wreg(c, 5, R05);
  wreg(c, 7, R07);
#if FR_HIDDEN
  {
    static const uint8_t hid[] = {0x12, 0x86, 0x60, 0x16, 0x44, 0x00, 0x1B, 0x8A, 0xDE, 0x36, 0x71, 0x14,
                                  0x39, 0x1A, 0x16, 0x3B, 0x27, 0x3C, 0x3C, 0x3C, 0x41, 0x3D, 0x04, 0x46,
                                  0x3E, 0x1E, 0x12, 0x3F, 0x14, 0x28, 0x43, 0x02, 0x02};
    for (i = 0; i < sizeof hid; i += 3) {
      wreg(c, hid[i], (uint32_t)hid[i + 1] << 8 | hid[i + 2]);
      if (i == 6) um_wait_us(api, 200000);
    }
  }
#endif
  um_wait_us(api, 200000);
  if (rdn(c, 0, &id, 1)) {
    ret = -4;
    goto out_pins;
  }
  rdn(c, 0x0A, w, 2);
  if (id == 0xFFFF && w[0] == 0xFFFF && w[1] == 0xFFFF) {
    ret = -5;
    goto out;
  }
  /* verbose mode, where the variant has it (REG 56H; OPEN_MODE first if the write needs it) */
  rdn(c, 0x56, &v56, 1);
  wreg(c, 0x56, (v56 & 0xFF00u) | 0xFFu);
  rdn(c, 0x56, &b56, 1);
  if ((b56 & 0xFF) != 0xFF) {
    wreg(c, 6, 0x6000u);
    wreg(c, 0x56, (v56 & 0xFF00u) | 0xFFu);
    rdn(c, 0x56, &b56, 1);
  }
  c->verbose = (b56 & 0xFF) == 0xFF;
  p = um_hex(put(s->ln, "v "), id, 4);
  p = um_hex(put(p, " "), v56, 4);
  p = um_hex(put(p, ">"), b56, 4);
  say(c, put(p, c->verbose ? " ok" : " no"));

#if FR_PART == 1
  /* the sweep, then the FM_TRUE peaks, strongest first */
  for (n = 0; n < NSW; n++) {
    uint32_t r;
    tune(c, FR_SW_LO + n * FR_SW_STEP);
    r = look(c, FR_QUICK_MS);
    s->sw[n] = (uint8_t)(r & 0x7F);
    s->swf[n] = (uint8_t)(r & 0x80 ? 't' : '.');
  }
  row(c, 'w', s->sw, 0);
  row(c, 'f', s->swf, 1);
  s->nst = 0;
  for (i = 0; i < NSW; i++) {
    uint32_t r = s->sw[i], k, m;
    if (s->swf[i] != 't' || (i && s->sw[i - 1] >= r) || (i + 1 < NSW && s->sw[i + 1] > r)) continue;
    for (k = 0; k < s->nst && s->st[k].q >= r; k++)
      ;
    if (k == FR_NST) continue;
    if (s->nst < FR_NST) s->nst++;
    for (m = sizeof(st_t) * (s->nst - 1u); m > sizeof(st_t) * k; m--) /* (no memcpy in a module) */
      ((uint8_t *)s->st)[m + sizeof(st_t) - 1] = ((uint8_t *)s->st)[m - 1];
    for (m = 0; m < sizeof(st_t); m++) ((uint8_t *)&s->st[k])[m] = 0;
    s->st[k].ch = (uint16_t)(FR_SW_LO + i * FR_SW_STEP), s->st[k].q = (uint8_t)r;
  }

  for (n = 0, ret = 0; n < s->nst; n++) {
    listen(c, &s->st[n], FR_SCAN_MS, 0);
    name_line(c, &s->st[n]);
    ret += s->st[n].g > 0;
  }

#else
  {
    static const uint16_t chs[] = {FR_CH};
    for (n = 0; n < sizeof chs / sizeof chs[0] && n < FR_NST; n++) {
      s->st[n].ch = chs[n];
      listen(c, &s->st[n], FR_CLK_S * 1000u, 1);
    }
    ret = (int)n;
  }
#endif

out:
  if ((v56 & 0xFF) != 0xFF || v56 != b56) wreg(c, 0x56, v56);
  wreg(c, 6, 0);
  wreg(c, 2, R02_ON & ~(1u | R02_RDS));
out_pins:
  i2c_stop(c);
  wr(GPIOA + G_CFGR, (rd(GPIOA + G_CFGR) & ~MSCL) | (cfa & MSCL));
  wr(GPIOB + G_CFGR, (rd(GPIOB + G_CFGR) & ~MSDA) | (cfb & MSDA));
  wr(GPIOA + G_OMODE, (rd(GPIOA + G_OMODE) & ~SCL) | (oma & SCL));
  wr(GPIOB + G_OMODE, (rd(GPIOB + G_OMODE) & ~SDA) | (omb & SDA));
  wr(GPIOA + G_PULL, (rd(GPIOA + G_PULL) & ~MSCL) | (pua & MSCL));
  wr(GPIOB + G_PULL, (rd(GPIOB + G_PULL) & ~MSDA) | (pub & MSDA));
  wr(GPIOA + (oda & SCL ? G_SCR : G_CLR), SCL);
  wr(GPIOB + (odb & SDA ? G_SCR : G_CLR), SDA);
  p = dec(put(s->ln, "E n="), ret);
  say(c, dec(put(p, " ms="), (int32_t)(api->ms() - t0)));
  return ret;
}
