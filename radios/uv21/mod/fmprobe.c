/* fmprobe: what is the UV-21 Pro's I2C chip, and if it is a broadcast FM receiver (RDA5807),
   what can it give SURF? Receive only. The UV-21 port of the K6's c/k6/mod/fmprobe.c
   (docs/k6-fm-chip.md), cut to fit a 4 KB cable module.

   The stock firmware talks to a device "0x60" on SCL PA4 with SDA on PB6, the pin the BK4829's
   3-wire bus also uses (docs/uv21-characteristics.md). The BK4829 ignores SDA while its SCN (PB3)
   is high, which it is between register accesses, and a module runs alone in the main loop. The
   bus runs open-drain on the MCU's pull-ups, so the MCU never drives a line high against the
   chip; both pins' mode, output type, pull and output bits are put back at the end.

   The chip (hamlet's read of the stock image, 2026-11-25): an RDA5807-family register map at
   7-bit address 0x30 (bytes 0x60 / 0x61), reached one register at a time: a write is the
   register number and 16 bits; a read is the register number, a stop, then a read that runs on
   through the following registers. It also acknowledges 0x10, 0x11 and 0x40, but every register
   reads 0xFF until it is set up: the stock setup is a soft reset, REG 02H on, then 05H, 07H and
   hidden registers 12H-43H (copied below). This module sends that setup with the audio muted
   (DMUTE 0, volume 0), never touches the amplifier pin (PB2), and powers the chip down at the
   end. It feeds on the BK4829's 26 MHz reference (CLK_MODE 110, as stock).

   Lines (the K6 module's tags, so tools/k6_fmprobe.py report reads both):
     P 119 319 2 5 30 50 115 6 7      the settings: sweep lo hi step, shape kHz n, expert lo hi,
                                      clock, part
     A 10 11 30 40                    every 7-bit address 0x08-0x77 that acknowledges
     a 10=1 11=1 60=0                 the three a plain RDA5807 answers
     b30 <32 hex>                     16 bytes read from each address that answered (up to 6)
     d00..                            REG 00H-5FH before the setup (all ffff on our radio)
     i 5804 4000 1800                 after the setup: REG 00H, 0AH and 0BH
     D00..                            REG 00H-5FH after the setup and the first tune
     w0/w1 f0/f1 s l c k h0/h1 x0/x1 v r g p t   as on the K6 (see c/k6/mod/fmprobe.c)
     E n=9 ms=123456                  the end
   Returns the sweep's FM_TRUE count, or -4 nothing answered, -5 every read 0xFF even after the
   setup, -6 too little RAM.

   Settings (-D, modem/uv21mod.py; the bench's uv21.mod step):
     FP_ADDR       the chip's 7-bit address (0x30, the stock firmware's)
     FP_CLK        CLK_MODE, REG 02H bits 6:4 (6 = 26 MHz, stock, the K6's and Aura's; 0 = 32.768 kHz,
                   1 = 12, 2 = 13, 3 = 19.2, 5 = 24, 7 = 38.4 MHz)
     FP_QUICK_MS / FP_SETTLE_MS   waits before a quick / settled read (150 / 600)
     FP_SW_LO, FP_SW_HI, FP_SW_STEP   sweep in 100 kHz channels above 76 MHz (119, 319, 2)
     FP_SHAPE_KHZ, FP_SHAPE_N     expert shape step and steps each side (5, 30)
     FP_EX_LO, FP_EX_HI           expert sweep, MHz (50, 115)
     FP_RDS1_S     RDS listen on the strongest station (60; a clock group comes once a minute)
     FP_RDS_S, FP_NRDS            listen on the next FP_NRDS - 1 stations (20, 1)
     FP_NEWM       REG 02H NEW_METHOD, as on the K6 (1; the stock firmware leaves it 0)
     FP_INTMODE    REG 05H INT_MODE (1; stock 0, where RDSR is a 5 ms pulse the RDS poll misses)
     FP_RDS_CH     RDS on these 100 kHz channels above 76 MHz (305 = 106.5 MHz) instead of the peaks
     FP_R04        a value for REG 04H (unset: not written, as stock)
     FP_HIDDEN     0 skips the stock writes to the unlisted registers 12H-43H (1)
     FP_PART       what runs besides the sweep and its peaks, a sum of: 1 the address scan, the
                   raw reads and the register dumps; 2 LNA ports, settling and the 50 kHz shape;
                   4 the expert rows; 8 verbose mode and RDS. 4 KB holds 7 or 8, not both: run
                   FP_PART=7 (about 50 s), then FP_PART=8 (about 20 s plus the RDS listens).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_OPT z
#include "uv21_mod.h"

#ifndef FP_HALF_US
#define FP_HALF_US 10
#endif
#ifndef FP_CLK
#define FP_CLK 6
#endif
#ifndef FP_QUICK_MS
#define FP_QUICK_MS 150
#endif
#ifndef FP_SETTLE_MS
#define FP_SETTLE_MS 600
#endif
#ifndef FP_SW_LO
#define FP_SW_LO 119
#endif
#ifndef FP_SW_HI
#define FP_SW_HI 319
#endif
#ifndef FP_SW_STEP
#define FP_SW_STEP 2
#endif
#ifndef FP_SHAPE_KHZ
#define FP_SHAPE_KHZ 5
#endif
#ifndef FP_SHAPE_N
#define FP_SHAPE_N 30
#endif
#ifndef FP_EX_LO
#define FP_EX_LO 50
#endif
#ifndef FP_EX_HI
#define FP_EX_HI 115
#endif
#ifndef FP_RDS1_S
#define FP_RDS1_S 60
#endif
#ifndef FP_RDS_S
#define FP_RDS_S 20
#endif
#ifndef FP_NRDS
#define FP_NRDS 1
#endif
#ifndef FP_PART
#define FP_PART 7
#endif
#ifndef FP_NEWM /* REG 02H NEW_METHOD (bit 2), as the K6's probe (and Aura) set it; stock doesn't */
#define FP_NEWM 1
#endif
#ifndef FP_HIDDEN /* 0 leaves out the stock writes to registers 12H-43H the public sheet doesn't list */
#define FP_HIDDEN 1
#endif
#ifndef FP_INTMODE /* REG 05H INT_MODE (bit 15): 1 holds RDSR until the blocks are read, as the K6 runs it;
                      stock leaves it 0, where RDSR is a 5 ms pulse a poll mostly misses */
#define FP_INTMODE 1
#endif
#ifndef FP_ADDR
#define FP_ADDR 0x30
#endif

#define NSW ((FP_SW_HI - FP_SW_LO) / FP_SW_STEP + 1)
#define NSH (2 * FP_SHAPE_N + 1)
#define NX (FP_EX_HI - FP_EX_LO + 1)
#define NC 50
#define NTOP 8
#if FP_SW_HI > 320 || FP_SW_LO >= FP_SW_HI || FP_SW_STEP < 1 || NSW > 128
#error "FP_SW_*: 100 kHz channels above 76 MHz, at most 128 steps, up to 320"
#endif
#if FP_EX_LO < 50 || FP_EX_HI > 115 || FP_EX_HI <= FP_EX_LO || NSH > 81 || FP_SHAPE_KHZ < 1
#error "FP_EX_* inside 50-115 MHz, FP_SHAPE_N at most 40"
#endif
#if FP_NRDS > NTOP - 1 || FP_CLK > 7 || FP_HALF_US < 2 || FP_PART > 15
#error "FP_NRDS at most 7, FP_CLK 0-7, FP_HALF_US at least 2"
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
#define SCL (1u << 4)     /* PA4 */
#define SDA (1u << 6)     /* PB6 */
#define MSCL (3u << 8)
#define MSDA (3u << 12)

#define A_RND (FP_ADDR << 1)

/* the stock firmware's setup (hamlet's read of the stock image, 2026-11-25), muted: 02 has DMUTE
   0 instead of 1 and RDS_EN added, 05 has volume 0 instead of 15 */
#define R02_ON (0x8009u | (FP_CLK << 4) | (FP_NEWM ? 4u : 0)) /* DHIZ, DMUTE 0 (muted), CLK_MODE, RDS_EN, NEW_METHOD, ENABLE */
#define R02_RDS 0x0008u
#define R05 (0x00E0u | (FP_INTMODE ? 0x8000u : 0)) /* stock (SEEKTH 0, both LNA inputs, LNA current 10) at volume 0, + INT_MODE */
#define R07 0x0202u   /* stock */
#define BAND_W 2u
#define BAND_S 3u
#define SP_100 0u
#define SP_50 2u

typedef struct {
  uint8_t sw[NSW], swf[NSW], top[NTOP], topr[NTOP];
#if FP_PART & 2
  uint8_t k[9];
#endif
#if FP_PART & 4
  uint8_t sh[NSH], x[NX];
#endif
  uint16_t sh5[7]; /* the shadow of REG 02H-08H */
} sc_t;

typedef struct {
  const um_api_t *a;
  sc_t *s;
} ctx_t;

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

/* ---- the bus ---- */
static void hb(ctx_t *c) { c->a->delay_us(FP_HALF_US); }
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
static int begin(ctx_t *c, uint32_t addr) {
  i2c_start(c);
  return i2c_wb(c, addr);
}

static int32_t rreg(ctx_t *c, uint32_t reg) {
  uint32_t hi, lo;
  if (!begin(c, A_RND) || !i2c_wb(c, reg)) {
    i2c_stop(c);
    return -1;
  }
  i2c_stop(c);
  if (!begin(c, A_RND | 1)) {
    i2c_stop(c);
    return -1;
  }
  hi = i2c_rb(c, 1);
  lo = i2c_rb(c, 0);
  i2c_stop(c);
  return (int32_t)(hi << 8 | lo);
}
static void rwr(ctx_t *c, uint32_t reg, uint32_t v) {
  begin(c, A_RND);
  i2c_wb(c, reg);
  i2c_wb(c, v >> 8);
  i2c_wb(c, v & 0xFF);
  i2c_stop(c);
}
static void wreg(ctx_t *c, uint32_t reg, uint32_t v) {
  if (reg - 2u < 7u) c->s->sh5[reg - 2] = (uint16_t)v;
  rwr(c, reg, v);
}
/* REG 0AH on: the register number, a stop, then a read that runs on (the stock status read) */
static void seqrd(ctx_t *c, uint16_t *w, int n) {
  int i;
  begin(c, A_RND);
  i2c_wb(c, 0x0A);
  i2c_stop(c);
  begin(c, A_RND | 1);
  for (i = 0; i < n; i++) {
    uint32_t hi = i2c_rb(c, 1);
    w[i] = (uint16_t)(hi << 8 | i2c_rb(c, i < n - 1));
  }
  i2c_stop(c);
}

/* ---- text ---- */
static char *dec(char *p, int32_t v) { return um_int(p, v); }
static char *put(char *p, const char *s) { return um_put(p, s); }
static char *khz(char *p, uint32_t k) { /* 104300 -> "104.300" */
  p = um_int(p, (int32_t)(k / 1000u));
  *p++ = '.';
  k %= 1000u;
  *p++ = (char)('0' + k / 100u);
  *p++ = (char)('0' + k / 10u % 10u);
  *p++ = (char)('0' + k % 10u);
  return p;
}
static void say0(ctx_t *c, char *line, char *p) {
  *p = 0;
  c->a->say(line);
}
static char rc(uint32_t r) { return (char)('0' + (r >> 1)); }
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
static uint32_t ch_khz(uint32_t ch) { return 76000u + ch * 100u; }
static void tune_ch(ctx_t *c, uint32_t space, uint32_t ch) {
  if (c->s->sh5[5] != R07) wreg(c, 7, R07);
  wreg(c, 3, ch << 6 | 0x10u | BAND_W << 2 | space);
}
static __attribute__((unused)) void tune_ex(ctx_t *c, uint32_t k) {
  wreg(c, 8, k - 50000u);
  wreg(c, 7, (R07 & ~0x0200u) | 1u);
  wreg(c, 3, 0x10u | BAND_S << 2);
}
/* wait ms, then RSSI (bits 6..0) with FM_TRUE in bit 7 and stereo in bit 8 */
static uint32_t look(ctx_t *c, uint32_t ms) {
  uint16_t w[2];
  um_wait_us(c->a, ms * 1000u);
  seqrd(c, w, 2);
  return (uint32_t)(w[1] >> 9) | (w[1] & 0x100u ? 0x80u : 0) | (w[0] & 0x400u ? 0x100u : 0);
}
#if FP_PART & 1
static void dump(ctx_t *c, char t) {
  char line[64], *p;
  uint32_t r;
  for (r = 0; r < 0x60; r += 11) {
    uint32_t i;
    p = line;
    *p++ = t;
    p = um_hex(p, r, 2);
    for (i = r; i < r + 11 && i < 0x60; i++) {
      int32_t v = rreg(c, i);
      *p++ = ' ';
      p = v < 0 ? put(p, "----") : um_hex(p, (uint32_t)v, 4);
    }
    say0(c, line, p);
  }
}

#endif

#if FP_PART & 8
static void rds(ctx_t *c, uint32_t ch, uint32_t secs, int verbose) {
  uint16_t w[6], ps[4], cand[4], last[3] = {0, 0, 0};
  uint8_t gt[32], conf = 0;
  uint32_t g = 0, ok = 0, pl = 0, sy = 0, t0, i, prev = 0, pi = 0, r;
  char line[64], *p;
  for (i = 0; i < 32; i++) gt[i] = 0;
  for (i = 0; i < 4; i++) ps[i] = cand[i] = 0x2020;
  tune_ch(c, SP_100, ch);
  r = look(c, FP_SETTLE_MS) & 0x7F;
  wreg(c, 2, R02_ON & ~R02_RDS);
  wreg(c, 2, R02_ON);
  t0 = c->a->ms();
  while (c->a->ms() - t0 < secs * 1000u) {
    uint32_t e, rdy, ty;
    c->a->delay_us(5000);
    seqrd(c, w, 6);
    pl++;
    if (w[0] & 0x1000u) sy++; /* RDSS on any poll: the decoder in sync, groups seen or not */
    rdy = w[0] >> 15;
    if (!rdy || prev) {
      prev = rdy;
      continue;
    }
    prev = 1;
    g++;
    e = (uint32_t)(w[1] & 15u) << 4;
    if (verbose) {
      int32_t v = rreg(c, 0x10);
      if (v >= 0) e |= ((uint32_t)v >> 12) & 15u;
    }
    if (!(e >> 4)) pi = w[2];
    if (!e) ok++;
    ty = w[3] >> 11;
    if (gt[ty] < 255) gt[ty]++;
    if ((ty >> 1) == 0 && !(e & 3)) {
      uint32_t s = w[3] & 3u;
      if (cand[s] == w[5]) ps[s] = w[5], conf |= (uint8_t)(1u << s);
      cand[s] = w[5];
    }
    if (ty == 8 && !(last[0] == w[3] && last[1] == w[4] && last[2] == w[5])) {
      uint32_t hh = (uint32_t)(w[4] & 1u) << 4 | w[5] >> 12, mm = (w[5] >> 6) & 63u;
      int off = (int)(w[5] & 31u);
      if (w[5] & 32u) off = -off;
      p = khz(put(line, "t "), ch_khz(ch));
      p = dec(put(p, " mjd="), (int32_t)((uint32_t)(w[3] & 3u) << 15 | w[4] >> 1));
      *p++ = ' ';
      *p++ = (char)('0' + hh / 10u);
      *p++ = (char)('0' + hh % 10u);
      *p++ = ':';
      *p++ = (char)('0' + mm / 10u);
      *p++ = (char)('0' + mm % 10u);
      p = dec(put(p, " o="), off);
      p = put(p, " e=");
      for (i = 0; i < 4; i++) *p++ = (char)('0' + ((e >> (6 - 2 * i)) & 3));
      say0(c, line, p);
      last[0] = w[3], last[1] = w[4], last[2] = w[5];
    }
  }
  p = khz(put(line, "r "), ch_khz(ch));
  p = dec(put(p, " q"), (int32_t)r);
  p = dec(put(p, " g"), (int32_t)g);
  p = dec(put(p, " ok"), (int32_t)ok);
  p = dec(put(p, " pl"), (int32_t)pl);
  say0(c, line, dec(put(p, " sy"), (int32_t)sy));
  p = put(line, "g");
  for (i = 0; i < 32 && p < line + 52; i++)
    if (gt[i]) {
      *p++ = ' ';
      p = dec(p, (int32_t)(i >> 1));
      *p++ = (char)('A' + (i & 1));
      p = dec(p, gt[i]);
    }
  say0(c, line, p);
  if (g) {
    p = khz(put(line, "p "), ch_khz(ch));
    p = um_hex(put(p, " pi="), pi, 4);
    p = put(p, " ps=");
    for (i = 0; i < 8; i++) {
      uint32_t ch8 = i & 1 ? ps[i >> 1] & 0xFF : ps[i >> 1] >> 8;
      *p++ = (conf >> (i >> 1) & 1) && ch8 >= 32 && ch8 < 127 ? (char)ch8 : '.';
    }
    say0(c, line, p);
  }
}
#endif

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  ctx_t cx, *c = &cx;
  sc_t *s = (sc_t *)ram;
  uint32_t i, n, fl, cfa, cfb, oma, omb, pua, pub, oda, odb, t0, best = 0, heard = 0;
  int32_t id, v56 = -1;
  int ret = 0;
  char line[64], *p;
  if (ram_len < sizeof(sc_t)) return -6;
  c->a = api, c->s = s;
  t0 = api->ms();
  p = put(line, "P");
  {
    static const uint16_t pv[] = {FP_SW_LO, FP_SW_HI, FP_SW_STEP, FP_SHAPE_KHZ, FP_SHAPE_N, FP_EX_LO, FP_EX_HI, FP_CLK, FP_PART};
    for (i = 0; i < 9; i++) p = dec(put(p, " "), pv[i]);
  }
  say0(c, line, p);

  /* the pins: save, then open-drain outputs with pull-ups, released */
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

#if FP_PART & 1
  uint32_t nack = 0;
  uint8_t acks[6];
  int a10 = 0, a11 = 0, a60 = 0;
  /* who answers */
  p = put(line, "A");
  for (i = 8; i < 0x78; i++) {
    int ack = begin(c, i << 1);
    i2c_stop(c);
    if (!ack) continue;
    if (i == 0x10) a10 = 1;
    if (i == 0x11) a11 = 1;
    if (i == 0x60) a60 = 1;
    if (nack < 6) acks[nack++] = (uint8_t)i;
    if (p < line + 56) p = um_hex(put(p, " "), i, 2);
  }
  say0(c, line, p);
  p = put(line, "a 10=");
  *p++ = (char)('0' + a10);
  p = put(p, " 11=");
  *p++ = (char)('0' + a11);
  p = put(p, " 60=");
  *p++ = (char)('0' + a60);
  say0(c, line, p);
  for (n = 0; n < nack; n++) {
    p = um_hex(put(line, "b"), acks[n], 2);
    *p++ = ' ';
    if (begin(c, (uint32_t)acks[n] << 1 | 1u))
      for (i = 0; i < 16; i++) p = um_hex(p, i2c_rb(c, i < 15), 2);
    i2c_stop(c);
    say0(c, line, p);
  }
  if (!nack) {
    ret = -4;
    goto out_pins;
  }
  (void)a10, (void)a11, (void)a60;
  dump(c, 'd');
#endif

  /* power up the stock way, muted (the chip reads 0xFF until it is on), then the first channel */
  for (i = 0; i < 7; i++) s->sh5[i] = 0;
  {
    static const uint8_t late[] = {0x36, 0x71, 0x14, 0x39, 0x1A, 0x16, 0x3B, 0x27, 0x3C, 0x3C, 0x3C, 0x41,
                                   0x3D, 0x04, 0x46, 0x3E, 0x1E, 0x12, 0x3F, 0x14, 0x28, 0x43, 0x02, 0x02};
    wreg(c, 2, 2u);
    api->delay_us(10000);
    wreg(c, 2, R02_ON);
    api->delay_us(10000);
    wreg(c, 5, R05);
    wreg(c, 7, R07);
#ifdef FP_R04
    wreg(c, 4, FP_R04); /* REG 04H, which stock leaves at reset (the K6 runs 0x0400) */
#endif
#if FP_HIDDEN
    rwr(c, 0x12, 0x8660);
    rwr(c, 0x16, 0x4400);
    rwr(c, 0x1B, 0x8ADE);
    um_wait_us(api, 200000); /* (one delay_us call wraps past 139 ms at 120 MHz) */
    for (i = 0; i < sizeof late; i += 3) rwr(c, late[i], (uint32_t)late[i + 1] << 8 | late[i + 2]);
#else
    (void)late;
    um_wait_us(api, 200000); /* (one delay_us call wraps past 139 ms at 120 MHz) */
#endif
  }
  id = rreg(c, 0);
  {
    uint16_t w[2];
    seqrd(c, w, 2);
    p = um_hex(put(line, "i "), (uint32_t)id & 0xFFFF, 4);
    p = um_hex(put(p, " "), w[0], 4);
    say0(c, line, um_hex(put(p, " "), w[1], 4));
    if (id < 0) {
      ret = -4;
      goto out_pins;
    }
    if (id == 0xFFFF && w[0] == 0xFFFF && w[1] == 0xFFFF) {
      ret = -5;
      goto out;
    }
  }
  tune_ch(c, SP_100, FP_SW_LO);
  look(c, FP_QUICK_MS);
#if FP_PART & 1
  dump(c, 'D');
#endif

  /* the sweep */
  for (n = 0; n < NSW; n++) {
    uint32_t r;
    tune_ch(c, SP_100, FP_SW_LO + n * FP_SW_STEP);
    r = look(c, FP_QUICK_MS);
    s->sw[n] = (uint8_t)(r & 0x7F);
    s->swf[n] = (uint8_t)(r & 0x80 ? (r & 0x100 ? 'S' : 't') : r & 0x100 ? 's' : '.');
    if (r & 0x80) heard++;
  }
  row(c, 'w', s->sw, NSW, 0);
  row(c, 'f', s->swf, NSW, 1);

  /* peaks: local maxima 6 over the sweep's floor, strongest first */
  for (n = 0; n < NTOP; n++) s->top[n] = 0xFF;
  for (i = 0, fl = 127; i < NSW; i++)
    if (s->sw[i] < fl) fl = s->sw[i];
  for (i = 0; i < NSW; i++) {
    uint32_t r = s->sw[i], k;
    if ((i && s->sw[i - 1] >= r) || (i + 1 < NSW && s->sw[i + 1] > r) || r < fl + 6) continue;
    for (k = 0; k < NTOP && s->top[k] != 0xFF && s->sw[s->top[k]] >= r; k++)
      ;
    if (k == NTOP) continue;
    for (n = NTOP - 1; n > k; n--) s->top[n] = s->top[n - 1];
    s->top[k] = (uint8_t)i;
  }
  for (n = 0; n < NTOP && s->top[n] != 0xFF; n++) {
    uint32_t ch = FP_SW_LO + s->top[n] * FP_SW_STEP, r;
    tune_ch(c, SP_100, ch);
    r = look(c, FP_SETTLE_MS);
    s->topr[n] = (uint8_t)(r & 0x7F);
    p = khz(put(line, "s "), ch_khz(ch));
    p = dec(put(p, " q="), s->sw[s->top[n]]);
    p = dec(put(p, " r="), (int32_t)(r & 0x7F));
    *p++ = ' ';
    *p++ = r & 0x80 ? 't' : '.';
    *p++ = r & 0x100 ? 'S' : '.';
    say0(c, line, p);
    if (s->topr[n] > s->topr[best]) best = n;
  }
  if (s->top[0] == 0xFF) goto out;
  best = FP_SW_LO + s->top[best] * FP_SW_STEP;

#if FP_PART & 2
  {
    uint32_t lr[4], k;
    int lna = 3;
    p = put(line, "l");
    for (k = 0; k < 4; k++) {
      wreg(c, 5, (R05 & ~0xC0u) | k << 6);
      tune_ch(c, SP_100, best);
      lr[k] = look(c, FP_SETTLE_MS) & 0x7F;
      p = dec(put(p, " "), (int32_t)lr[k]);
    }
    say0(c, line, p);
    for (k = 0; k < 4; k++)
      if (lr[k] >= lr[lna] + 6) lna = (int)k; /* (3, both, is the stock choice) */
    wreg(c, 5, (R05 & ~0xC0u) | (uint32_t)lna << 6);
  }

  /* settling: from a far channel to it, RSSI every 20 ms */
  tune_ch(c, SP_100, best > FP_SW_LO + 40 ? FP_SW_LO : FP_SW_HI);
  look(c, FP_SETTLE_MS);
  {
    uint16_t w[2];
    uint32_t k, tt = api->ms();
    tune_ch(c, SP_100, best);
    for (k = 0; k < 200; k++) {
      seqrd(c, w, 2);
      if (w[0] & 0x4000u) break;
      api->delay_us(500);
    }
    p = dec(put(line, "c "), (int32_t)(api->ms() - tt));
    *p++ = ' ';
    for (k = 0; k < NC; k++) {
      api->delay_us(20000);
      seqrd(c, w, 2);
      *p++ = rc(w[1] >> 9);
    }
    say0(c, line, p);
  }

  for (n = 0; n < 9; n++) {
    tune_ch(c, SP_50, best * 2u - 4u + n);
    s->k[n] = (uint8_t)(look(c, FP_QUICK_MS) & 0x7F);
  }
  row(c, 'k', s->k, 9, 0);
#endif

#if FP_PART & 4
  {
    uint32_t f0 = ch_khz(best) - FP_SHAPE_N * FP_SHAPE_KHZ;
    for (n = 0; n < NSH; n++) {
      tune_ex(c, f0 + n * FP_SHAPE_KHZ);
      s->sh[n] = (uint8_t)(look(c, FP_QUICK_MS) & 0x7F);
    }
    row(c, 'h', s->sh, NSH, 0);
    for (n = 0; n < NX; n++) {
      tune_ex(c, (FP_EX_LO + n) * 1000u);
      s->x[n] = (uint8_t)(look(c, FP_QUICK_MS) & 0x7F);
    }
    row(c, 'x', s->x, NX, 0);
  }
#endif

#if FP_PART & 8
  int verbose;
  {
    int32_t b;
    v56 = rreg(c, 0x56);
    p = um_hex(put(line, "v "), (uint32_t)v56 & 0xFFFF, 4);
    rwr(c, 0x56, ((uint32_t)v56 & 0xFF00u) | 0xFFu);
    b = rreg(c, 0x56);
    if ((b & 0xFF) != 0xFF) {
      wreg(c, 6, 0x6000u);
      rwr(c, 0x56, ((uint32_t)v56 & 0xFF00u) | 0xFFu);
      b = rreg(c, 0x56);
      p = put(p, " om");
    }
    verbose = (b & 0xFF) == 0xFF;
    *p++ = '>';
    p = um_hex(p, (uint32_t)b & 0xFFFF, 4);
    say0(c, line, put(p, verbose ? " ok" : " no"));
  }
#ifdef FP_RDS_CH
  {
    static const uint16_t rch[] = {FP_RDS_CH}; /* the stations to hear, not the sweep's peaks */
    for (n = 0; n < sizeof rch / sizeof rch[0]; n++) rds(c, rch[n], n ? FP_RDS_S : FP_RDS1_S, verbose);
  }
#else
  for (n = 0; n < FP_NRDS && s->top[n] != 0xFF; n++)
    rds(c, FP_SW_LO + s->top[n] * FP_SW_STEP, n ? FP_RDS_S : FP_RDS1_S, verbose);
#endif
#endif
  ret = (int)heard;

out:
  if (v56 >= 0) rwr(c, 0x56, (uint32_t)v56);
  if (s->sh5[4]) wreg(c, 6, 0);
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
  p = dec(put(line, "E n="), ret);
  say0(c, line, dec(put(p, " ms="), (int32_t)(api->ms() - t0)));
  return ret;
}
