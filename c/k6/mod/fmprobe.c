/* fmprobe: what can the K6's broadcast FM chip do? Receive only (docs/k6-fm-chip.md).

   The K6 carries an RDA5807M beside the FD6818 (Aura edf3b98 src/board/fm.rs: bit-banged I2C,
   SCL PA4, SDA PA12). Aura only tunes, seeks and reads its RSSI; the pager images never touch
   it. This module finds out, on our radio, what the chip offers a SURF-style band view and a
   clock: the RSSI's settling time and the shape of one station across the chip's own filter
   (the lens size), the antenna input that works, the 50-115 MHz "expert" tuning (OE3HBW), and
   RDS with the hidden verbose mode (REG 0x56 = 0xAEFF, Zsolt Kovari on hackaday.io project 9009)
   that gives every block's error level, including the clock group 4A.

   The bus runs open-drain with the MCU's pull-ups (the MCU never drives a line high against
   the chip), at about 1 / (4 half_us) bit/s. PA4 / PA12's mode, type, speed, pull and output
   bits are saved first and put back at the end, whatever happens. The chip's audio stays muted
   (DMUTE 0, volume 0), the speaker amplifier is never touched, and at the end the chip is
   powered down with REG 0x56 and OPEN_MODE as they were.

   Steps (each line is a serial say line; the parameter block PAR below sets the numbers):
     a 10=1 11=1 60=0                 who acknowledges: sequential 0x10, random 0x11, TEA5767 0x60
     d00 5804 ....                    registers 0x00-0x5F before power-up, 11 a line (random access;
                                      0x56 is the verbose switch)
     D00 ....                         the same after power-up and the first tune
     w0 / w1 <chars>                  the band sweep, one char per channel: '0' + RSSI / 2
     f0 / f1 <chars>                  its flags: '.' none, 't' FM_TRUE, 's' stereo, 'S' both
     s 104.300 q=52 r=58 tS           a peak: quick read, settled read, flags (top 8)
     l 3 40 72 70                     the strongest station's settled RSSI on LNA ports 0..3
     c 12 <50 chars>                  settling: STC after 12 ms, then RSSI every 20 ms for 1 s
     k <9 chars>                      station shape, channel tuning at 50 kHz: -200..+200 kHz
     h0 / h1 <chars>                  station shape, expert tuning at shape_khz: -n..+n steps
     x0 / x1 <chars>                  expert sweep 50-115 MHz, 1 MHz steps
     v aec0>aeff ok                   verbose mode (REG 0x56); "om" when OPEN_MODE was needed
     r 104.300 q58 g212 ok180 ab200 ss150   a station's RDS: groups, all four blocks clean, A+B
                                      clean, groups with the decoder synchronised
     g 0A112 2A40 4A1                 group types heard (counts to 255)
     p 104.300 pi=1234 ps=KXYZ-FM     PI and the station name, each PS pair confirmed twice
     t 104.300 mjd=61004 01:42 o=-16 e=0000   a clock group the moment it arrives: MJD, UTC,
                                      local offset in half hours, BLER A-D (0 = clean)
     E n=9 ms=123456                  the end
   Returns the stations with FM_TRUE in the sweep, or -2 a key, -3 an image below level 2,
   -4 nothing acknowledged, -5 a chip ID that isn't 0x58xx, -6 no scratch, -11 a bad block.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT z
#include "../k6_mod.h"

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
#define M2 ((3u << 8) | (3u << 24)) /* the two pins' 2-bit fields */

#define A_SEQ 0x20u /* 0x10 << 1 */
#define A_RND 0x22u /* 0x11 << 1 */
#define A_TEA 0xC0u /* 0x60 << 1 */

#define R02_ON 0x806Du /* DHIZ, DMUTE 0 (muted), CLK_MODE 26 MHz (Aura's), RDS_EN, NEW_METHOD, ENABLE */
#define R02_RDS 0x0008u
#define R04 0x0400u    /* Aura's */
#define R05 0x8880u    /* INT_MODE, SEEKTH 8, LNA_PORT_SEL 10 (LNAP, Aura's), volume 0 */
#define R07 0x4202u    /* the datasheet's default: soft blend 32 dB, 65M_50M 1, SOFTBLEND_EN */
#define BAND_W 2u      /* 76-108 MHz */
#define BAND_S 3u      /* 65-76, or 50-76 with 65M_50M 0 */
#define SP_100 0u
#define SP_50 2u
#define NSW 128
#define NSH 96
#define NX 72
#define NC 50
#define NTOP 8

typedef struct {
  char magic[8];      /*  0 "FMPROBE1" */
  uint16_t half_us;   /*  8 I2C half bit */
  uint16_t quick_ms;  /* 10 wait after a tune before a quick read */
  uint16_t settle_ms; /* 12 wait for a settled read (OE3HBW: 600) */
  uint16_t rds1_s;    /* 14 RDS listen on the strongest (a clock group comes once a minute) */
  uint16_t rds_s;     /* 16 RDS listen on the others */
  uint16_t sw_lo;     /* 18 sweep, 100 kHz channels above 76 MHz: 119 = 87.9 MHz */
  uint16_t sw_hi;     /* 20 319 = 107.9 MHz */
  uint8_t sw_step;    /* 22 channels a step (2 = 200 kHz) */
  uint8_t n_rds;      /* 23 strongest stations for RDS, plus the weakest peak */
  uint8_t shape_khz;  /* 24 expert shape step */
  uint8_t shape_n;    /* 25 steps each side */
  uint8_t ex_lo;      /* 26 expert sweep, MHz */
  uint8_t ex_hi;      /* 27 */
  uint8_t flags;      /* 28 1 no verbose write, 2 no LNA test, 4 no expert, 8 no RDS */
  uint8_t pad[3];     /* 29 */
} par_t;              /* 32 bytes */

static const volatile par_t PAR = {{'F', 'M', 'P', 'R', 'O', 'B', 'E', '1'},
                                   10, 150, 600, 65, 20, 119, 319, 2, 3, 5, 40, 50, 115, 0, {0, 0, 0}};

typedef struct {
  uint8_t sw[NSW], swf[NSW], sh[NSH], k[12], x[NX], c[NC];
  uint8_t top[NTOP], topr[NTOP];
  uint16_t sh5[9];  /* the shadow of REG 02H-08H (sh5[0] = 02H; sh5[7], sh5[8] spare) */
} sc_t;

typedef struct {
  const k6_api_t *a;
  sc_t *s;
  uint32_t half;
  int ra; /* random access works */
} ctx_t;

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

/* ---- the bus ---- */
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
static int i2c_wb(ctx_t *c, uint32_t b) { /* 1 = acknowledged */
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

/* one register by random access: its value, or -1 */
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
/* REG 02H-08H through the shadow: random access, or the sequential write from 02H up to reg */
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
/* REG 0AH.. by the sequential read */
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
static char *dec(char *p, uint32_t v) {
  static const uint32_t pw[] = {1000000000, 100000000, 10000000, 1000000, 100000,
                                10000,      1000,      100,      10,      1};
  int i, started = 0;
  for (i = 0; i < 10; i++) {
    int d = 0;
    while (v >= pw[i]) v -= pw[i], d++;
    if (d || started || i == 9) *p++ = (char)('0' + d), started = 1;
  }
  return p;
}
static char *sdec(char *p, int v) {
  if (v < 0) *p++ = '-', v = -v;
  return dec(p, (uint32_t)v);
}
static char *hex(char *p, uint32_t v, int digits) {
  int i;
  for (i = (digits - 1) * 4; i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}
static char *khz(char *p, uint32_t k) { /* 104300 -> "104.300" */
  static const uint32_t pw[] = {100000, 10000, 1000, 100, 10, 1};
  int i, started = 0;
  for (i = 0; i < 6; i++) {
    int d = 0;
    while (k >= pw[i]) k -= pw[i], d++;
    if (d || started || i >= 2) *p++ = (char)('0' + d), started = 1;
    if (i == 2) *p++ = '.';
  }
  return p;
}
static void say0(ctx_t *c, char *line, char *p) {
  *p = 0;
  c->a->say(line);
}
static char rc(uint32_t r) { return (char)('0' + (r > 127 ? 63 : r >> 1)); }
/* a row of chars in lines of at most 50, tagged t0, t1 .. */
static void row(ctx_t *c, char t, const uint8_t *v, int n, int raw) {
  char line[64], *p;
  int i = 0, part = 0;
  while (i < n) {
    int j;
    p = line;
    *p++ = t;
    if (n > 50) *p++ = (char)('0' + part);
    *p++ = ' ';
    for (j = 0; j < 50 && i < n; j++, i++) *p++ = raw ? (char)v[i] : rc(v[i]);
    say0(c, line, p);
    part++;
  }
}

/* ---- the chip ---- */
static int key(ctx_t *c) {
  return (c->a->level >= 4 ? c->a->key_poll() : c->a->key_down()) || km_power_switch_off();
}
static uint32_t ch_khz(uint32_t ch) { return 76000u + ch * 100u; }

/* status: REG 0AH, 0BH */
static void stat(ctx_t *c, uint16_t *w) { seqrd(c, w, 2); }

static void tune_ch(ctx_t *c, uint32_t band, uint32_t space, uint32_t ch) {
  if (c->s->sh5[5] != R07) wreg(c, 7, R07);
  wreg(c, 3, ch << 6 | 0x10u | band << 2 | space);
}
/* expert tuning (OE3HBW): band 11, 65M_50M 0, FREQ_MODE 1, freq_direct = kHz above 50 MHz */
static void tune_ex(ctx_t *c, uint32_t k) {
  wreg(c, 8, k - 50000u);
  wreg(c, 7, (R07 & ~0x0200u) | 1u);
  wreg(c, 3, 0x10u | BAND_S << 2);
}
/* wait ms, then RSSI (bits 6..0) with FM_TRUE in bit 7 and stereo in bit 8 */
static uint32_t look(ctx_t *c, uint32_t ms) {
  uint16_t w[2];
  c->a->delay_us(ms * 1000u);
  stat(c, w);
  return (uint32_t)(w[1] >> 9) | (w[1] & 0x100u ? 0x80u : 0) | (w[0] & 0x400u ? 0x100u : 0);
}
static void dump(ctx_t *c, char t) {
  char line[64], *p;
  uint32_t r;
  for (r = 0; r < 0x60; r += 11) {
    uint32_t i;
    p = line;
    *p++ = t;
    p = hex(p, r, 2);
    for (i = r; i < r + 11 && i < 0x60; i++) {
      int32_t v = rreg(c, i);
      *p++ = ' ';
      p = v < 0 ? put(p, "----") : hex(p, (uint32_t)v, 4);
    }
    say0(c, line, p);
  }
}

/* RDS on one station: counts, PI / PS (each pair confirmed twice), a line per clock group */
static int rds(ctx_t *c, uint32_t ch, uint32_t secs, uint32_t settle, int verbose) {
  uint16_t w[6], ps[4], cand[4], last[4] = {0, 0, 0, 0};
  uint8_t gt[32], conf = 0;
  uint32_t g = 0, ok = 0, ab = 0, ss = 0, t0, i, prev = 0, pi = 0, r;
  char line[64], *p;
  for (i = 0; i < 32; i++) gt[i] = 0;
  for (i = 0; i < 4; i++) ps[i] = cand[i] = 0x2020;
  tune_ch(c, BAND_W, SP_100, ch);
  r = look(c, settle) & 0x7F;
  wreg(c, 2, R02_ON & ~R02_RDS); /* restart the decoder (hackaday.io 9009) */
  wreg(c, 2, R02_ON);
  t0 = c->a->ms();
  while (c->a->ms() - t0 < secs * 1000u) {
    uint32_t e = 0, rdy;
    if (key(c)) return -2;
    c->a->delay_us(5000);
    seqrd(c, w, 6);
    rdy = w[0] >> 15;
    if (!rdy || prev) {
      prev = rdy;
      continue;
    }
    prev = 1;
    g++;
    if (w[0] & 0x1000u) ss++;
    e = (uint32_t)(w[1] & 15u) << 4;
    if (verbose) {
      int32_t v = rreg(c, 0x10);
      if (v >= 0) e |= ((uint32_t)v >> 12) & 15u;
    }
    if (!(e >> 4)) ab++;
    if (!e) ok++;
    {
      uint32_t ty = w[3] >> 11; /* type << 1 | B */
      if (gt[ty] < 255) gt[ty]++;
      if (!(e >> 4)) pi = w[2];
      if ((ty >> 1) == 0 && !(e & 3)) { /* 0A / 0B: PS pair in D */
        uint32_t s = w[3] & 3u;
        if (cand[s] == w[5]) ps[s] = w[5], conf |= (uint8_t)(1u << s);
        cand[s] = w[5];
      }
      if (ty == 8) { /* 4A: the clock */
        uint32_t mjd = (uint32_t)(w[3] & 3u) << 15 | w[4] >> 1;
        uint32_t hh = (uint32_t)(w[4] & 1u) << 4 | w[5] >> 12, mm = (w[5] >> 6) & 63u;
        int off = (int)(w[5] & 31u);
        if (w[5] & 32u) off = -off;
        if (!(last[1] == w[3] && last[2] == w[4] && last[3] == w[5])) {
          p = put(line, "t ");
          p = khz(p, ch_khz(ch));
          p = put(p, " mjd=");
          p = dec(p, mjd);
          *p++ = ' ';
          *p++ = (char)('0' + (hh >= 20 ? 2 : hh >= 10 ? 1 : 0));
          *p++ = (char)('0' + (hh >= 20 ? hh - 20 : hh >= 10 ? hh - 10 : hh));
          *p++ = ':';
          p = dec(put(p, mm < 10 ? "0" : ""), mm);
          p = sdec(put(p, " o="), off);
          p = put(p, " e=");
          for (i = 0; i < 4; i++) *p++ = (char)('0' + ((e >> (6 - 2 * i)) & 3));
          say0(c, line, p);
        }
        last[1] = w[3], last[2] = w[4], last[3] = w[5];
      }
    }
  }
  p = put(line, "r ");
  p = khz(p, ch_khz(ch));
  p = dec(put(p, " q"), r);
  p = dec(put(p, " g"), g);
  p = dec(put(p, " ok"), ok);
  p = dec(put(p, " ab"), ab);
  say0(c, line, dec(put(p, " ss"), ss));
  p = put(line, "g");
  for (i = 0; i < 32 && p < line + 52; i++)
    if (gt[i]) {
      *p++ = ' ';
      p = dec(p, i >> 1);
      *p++ = (char)('A' + (i & 1));
      p = dec(p, gt[i]);
    }
  say0(c, line, p);
  if (g) {
    p = put(line, "p ");
    p = khz(p, ch_khz(ch));
    p = hex(put(p, " pi="), pi, 4);
    p = put(p, " ps=");
    for (i = 0; i < 4; i++) {
      uint32_t k;
      for (k = 0; k < 2; k++) {
        uint32_t ch8 = k ? ps[i] & 0xFF : ps[i] >> 8;
        *p++ = (conf >> i & 1) && ch8 >= 32 && ch8 < 127 ? (char)ch8 : '.';
      }
    }
    say0(c, line, p);
  }
  return (int)g;
}

int k6_main(const k6_api_t *api) {
  par_t q;
  ctx_t cx, *c = &cx;
  sc_t *s;
  uint8_t *d = (uint8_t *)&q;
  const volatile uint8_t *src = (const volatile uint8_t *)&PAR;
  uint32_t i, n, fl, bytes = 0, moder, otyper, ospeed, pupdr, odr, t0, best = 0, nsw, heard = 0;
  int32_t id, v56 = -1;
  int ret = 0, a10, a11, a60, lna = 2, verbose = 0;
  char line[64], *p;
  if (api->level < 2) return -3;
  for (i = 0; i < sizeof q; i++) d[i] = src[i];
  if (q.half_us < 2 || q.sw_step < 1 || q.sw_hi <= q.sw_lo || q.sw_hi > 320 ||
      q.shape_n * 2 + 1 > NSH || q.n_rds > NTOP - 1 || q.shape_khz < 1 ||
      q.ex_lo < 50 || q.ex_hi > 115 || q.ex_hi <= q.ex_lo || q.ex_hi - q.ex_lo >= NX)
    return -11;
  for (n = 0, i = q.sw_lo; i <= q.sw_hi; i += q.sw_step) {
    __asm__("" : "+r"(i)); /* no trip-count division (no __aeabi_uidiv here) */
    n++;
  }
  if (n > NSW) return -11;
  s = (sc_t *)api->scratch(&bytes);
  if (!s || bytes < sizeof(sc_t)) return -6;
  c->a = api, c->s = s, c->half = q.half_us, c->ra = 0;
  t0 = api->ms();

  /* the pins: save, then open-drain outputs with pull-ups, released */
  moder = rd(GPIOA + G_MODER), otyper = rd(GPIOA + G_OTYPER), ospeed = rd(GPIOA + G_OSPEEDR);
  pupdr = rd(GPIOA + G_PUPDR), odr = rd(GPIOA + G_ODR);
  wr(GPIOA + G_BSRR, SCL | SDA);
  wr(GPIOA + G_OTYPER, otyper | SCL | SDA);
  wr(GPIOA + G_PUPDR, (pupdr & ~M2) | (1u << 8) | (1u << 24));
  wr(GPIOA + G_OSPEEDR, ospeed | M2);
  wr(GPIOA + G_MODER, (moder & ~M2) | (1u << 8) | (1u << 24));
  hb(c);
  i2c_stop(c); /* a clean bus */

  a10 = probe(c, A_SEQ), a11 = probe(c, A_RND), a60 = probe(c, A_TEA);
  p = put(line, "a 10=");
  *p++ = (char)('0' + a10);
  p = put(p, " 11=");
  *p++ = (char)('0' + a11);
  p = put(p, " 60=");
  *p++ = (char)('0' + a60);
  say0(c, line, p);
  if (!a10 && !a11) {
    ret = -4;
    goto out_pins;
  }
  id = a11 ? rreg(c, 0) : -1;
  c->ra = id >= 0 && (id & 0xFF00) == 0x5800;
  if (c->ra) dump(c, 'd');
  else if (id >= 0) {
    p = hex(put(line, "i "), (uint32_t)id, 4);
    say0(c, line, p);
  }
  if (a11 && !c->ra) {
    ret = -5;
    goto out_pins;
  }

  /* power up muted, then the first channel */
  for (i = 0; i < 9; i++) s->sh5[i] = 0;
  s->sh5[2] = R04, s->sh5[3] = R05, s->sh5[5] = R07;
  wreg(c, 2, R02_ON | 2u); /* soft reset */
  wreg(c, 2, R02_ON);
  wreg(c, 4, R04);
  wreg(c, 5, R05);
  wreg(c, 7, R07);
  api->delay_us(q.settle_ms * 1000u);
  tune_ch(c, BAND_W, SP_100, q.sw_lo);
  look(c, q.quick_ms);
  if (c->ra) dump(c, 'D');

  /* the sweep */
  for (n = 0, i = q.sw_lo; i <= q.sw_hi; i += q.sw_step, n++) {
    uint32_t r;
    if (key(c)) {
      ret = -2;
      goto out;
    }
    tune_ch(c, BAND_W, SP_100, i);
    r = look(c, q.quick_ms);
    s->sw[n] = (uint8_t)(r & 0x7F);
    s->swf[n] = (uint8_t)(r & 0x80 ? (r & 0x100 ? 'S' : 't') : r & 0x100 ? 's' : '.');
    if (r & 0x80) heard++;
  }
  nsw = n;
  row(c, 'w', s->sw, (int)nsw, 0);
  row(c, 'f', s->swf, (int)nsw, 1);

  /* peaks: local maxima 6 over the sweep's floor, strongest first */
  for (n = 0; n < NTOP; n++) s->top[n] = 0xFF, s->topr[n] = 0;
  for (i = 0, fl = 127; i < nsw; i++)
    if (s->sw[i] < fl) fl = s->sw[i];
  for (i = 0; i < nsw; i++) {
    uint32_t r = s->sw[i], k;
    if ((i && s->sw[i - 1] > r) || (i + 1 < nsw && s->sw[i + 1] > r) || r < fl + 6) continue;
    if (i && s->sw[i - 1] == r) continue; /* a plateau counts once, at its first channel */
    for (k = 0; k < NTOP && s->top[k] != 0xFF && s->sw[s->top[k]] >= r; k++)
      ;
    if (k == NTOP) continue;
    for (n = NTOP - 1; n > k; n--) s->top[n] = s->top[n - 1];
    s->top[k] = (uint8_t)i;
  }
  for (n = 0; n < NTOP && s->top[n] != 0xFF; n++) {
    uint32_t ch = q.sw_lo + s->top[n] * q.sw_step, r;
    if (key(c)) {
      ret = -2;
      goto out;
    }
    tune_ch(c, BAND_W, SP_100, ch);
    r = look(c, q.settle_ms);
    s->topr[n] = (uint8_t)(r & 0x7F);
    p = put(line, "s ");
    p = khz(p, ch_khz(ch));
    p = dec(put(p, " q="), s->sw[s->top[n]]);
    p = dec(put(p, " r="), r & 0x7F);
    *p++ = ' ';
    *p++ = r & 0x80 ? 't' : '.';
    *p++ = r & 0x100 ? 'S' : '.';
    say0(c, line, p);
    if (s->topr[n] > s->topr[best]) best = n;
  }
  if (s->top[0] == 0xFF) goto out;
  best = q.sw_lo + s->top[best] * q.sw_step; /* the strongest station's channel */

  /* LNA ports on it; keep the best if it beats LNAP by 6 */
  if (!(q.flags & 2)) {
    uint32_t lr[4], k;
    p = put(line, "l");
    for (k = 0; k < 4; k++) {
      wreg(c, 5, (R05 & ~0xC0u) | k << 6);
      tune_ch(c, BAND_W, SP_100, best);
      lr[k] = look(c, q.settle_ms) & 0x7F;
      *p++ = ' ';
      p = dec(p, lr[k]);
    }
    say0(c, line, p);
    for (k = 0; k < 4; k++)
      if (lr[k] >= lr[lna] + 6) lna = (int)k;
    wreg(c, 5, (R05 & ~0xC0u) | (uint32_t)lna << 6);
  }

  /* settling: from a far channel to it, RSSI every 20 ms */
  tune_ch(c, BAND_W, SP_100, best > q.sw_lo + 40 ? q.sw_lo : q.sw_hi);
  look(c, q.settle_ms);
  {
    uint16_t w[2];
    uint32_t stc, k, tt = api->ms();
    tune_ch(c, BAND_W, SP_100, best);
    for (k = 0; k < 200; k++) {
      stat(c, w);
      if (w[0] & 0x4000u) break;
      api->delay_us(500);
    }
    stc = api->ms() - tt;
    for (k = 0; k < NC; k++) {
      api->delay_us(20000);
      stat(c, w);
      s->c[k] = (uint8_t)(w[1] >> 9);
    }
    p = dec(put(line, "c "), stc);
    *p++ = ' ';
    for (k = 0; k < NC; k++) *p++ = rc(s->c[k]);
    say0(c, line, p);
  }

  /* shape, channel tuning at 50 kHz (100 kHz channel ch is 50 kHz channel 2 ch; 25 kHz would
     overflow CHAN's 10 bits above 101.6 MHz) */
  for (n = 0; n < 9; n++) {
    tune_ch(c, BAND_W, SP_50, best * 2u - 4u + n);
    s->k[n] = (uint8_t)(look(c, q.quick_ms) & 0x7F);
  }
  row(c, 'k', s->k, 9, 0);

  if (!(q.flags & 4)) {
    uint32_t f0 = ch_khz(best), m = q.shape_n * 2u + 1u;
    for (n = 0; n < m; n++) {
      if (key(c)) {
        ret = -2;
        goto out;
      }
      tune_ex(c, f0 - q.shape_n * (uint32_t)q.shape_khz + n * q.shape_khz);
      s->sh[n] = (uint8_t)(look(c, q.quick_ms) & 0x7F);
    }
    row(c, 'h', s->sh, (int)m, 0);
    for (n = 0, i = q.ex_lo; i <= q.ex_hi; i++, n++) {
      if (key(c)) {
        ret = -2;
        goto out;
      }
      tune_ex(c, i * 1000u);
      s->x[n] = (uint8_t)(look(c, q.quick_ms) & 0x7F);
    }
    row(c, 'x', s->x, (int)n, 0);
  }

  /* verbose RDS, then the stations */
  if (c->ra && !(q.flags & 1)) {
    int32_t b;
    v56 = rreg(c, 0x56);
    p = hex(put(line, "v "), (uint32_t)v56 & 0xFFFF, 4);
    rwr(c, 0x56, ((uint32_t)v56 & 0xFF00u) | 0xFFu);
    b = rreg(c, 0x56);
    if ((b & 0xFF) != 0xFF) {
      wreg(c, 6, 0x6000u); /* OPEN_MODE 11 */
      rwr(c, 0x56, ((uint32_t)v56 & 0xFF00u) | 0xFFu);
      b = rreg(c, 0x56);
      p = put(p, " om");
    }
    verbose = (b & 0xFF) == 0xFF;
    *p++ = '>';
    p = hex(p, (uint32_t)b & 0xFFFF, 4);
    say0(c, line, put(p, verbose ? " ok" : " no"));
  }
  if (!(q.flags & 8)) {
    uint32_t done = 0, weak = 0;
    for (n = 0; n < NTOP && s->top[n] != 0xFF; n++) weak = n;
    for (n = 0; n < NTOP && s->top[n] != 0xFF; n++) {
      uint32_t ch = q.sw_lo + s->top[n] * q.sw_step;
      int r;
      if (n >= q.n_rds && n != weak) continue;
      r = rds(c, ch, done ? q.rds_s : q.rds1_s, q.settle_ms, verbose);
      if (r == -2) {
        ret = -2;
        goto out;
      }
      done++;
    }
  }
  ret = (int)heard;

out:
  if (v56 >= 0) rwr(c, 0x56, (uint32_t)v56);
  if (s->sh5[4]) wreg(c, 6, 0);
  wreg(c, 2, R02_ON & ~(1u | R02_RDS)); /* ENABLE 0 */
out_pins:
  i2c_stop(c);
  wr(GPIOA + G_MODER, (rd(GPIOA + G_MODER) & ~M2) | (moder & M2));
  wr(GPIOA + G_OTYPER, (rd(GPIOA + G_OTYPER) & ~(SCL | SDA)) | (otyper & (SCL | SDA)));
  wr(GPIOA + G_OSPEEDR, (rd(GPIOA + G_OSPEEDR) & ~M2) | (ospeed & M2));
  wr(GPIOA + G_PUPDR, (rd(GPIOA + G_PUPDR) & ~M2) | (pupdr & M2));
  wr(GPIOA + G_BSRR, (odr & SCL ? SCL : SCL << 16) | (odr & SDA ? SDA : SDA << 16));
  p = put(line, "E n=");
  p = sdec(p, ret);
  say0(c, line, dec(put(p, " ms="), api->ms() - t0));
  return ret;
}
