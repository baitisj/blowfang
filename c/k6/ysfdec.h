/* ysfdec.h: YSF (C4FM) frame fields on the K6 (docs/k6-c4fm.md, "Receiving C4FM sotto"). The
   integer version of modem/ysf.py's FICH and modem/fec.py's sotto VW decoders, for a RAM module
   (c/k6/mod/ysfdec.inc, c/k6/mod/ysfrx.c) and the host check (c/k6/ysfrx_host.c): header-only,
   no writable statics, no division, no 64-bit. Soft bits are 4 bits (-7..7, positive = bit 0),
   two a byte.

     sync     D4 71 C9 63 4D: 20 symbols, levels YD_SYNC (dibit 01 +3, 00 +1, 10 -1, 11 -3)
     FICH     100 symbols after it: a dibit interleaver (dibit i of the code -> field bits
              (i % 5) * 40 + (i / 5) * 2), a K=5 rate 1/2 code (taps 10011, 11101) over 96 bits
              and 4 flush bits: 4 Golay (24,12) words = 4 FICH bytes + CRC-16 CCITT (inverted).
              Only the Golay words' data halves are taken; the CRC checks them
     sotto VW the 360 symbols after that: XOR PN9 (seed 0x1FF), payload bit (c % 24) * 30 + c / 24
              = coded bit c, the K=7 code (133, 171, 165) punctured to 7/18, tail-biting over 5 x
              (48 bits + CRC-8 0x07) = 280 bits: five sotto 1200 superframes of 6 bytes

   Both decoders go a step at a time (yd_f5_step, yd_v7_step, then yd_v7_tb's traceback in
   pieces), so a module can spread them over the gaps between the DMA's samples. Both are
   butterflies (every generator has its first and last taps). The K=7 decisions take
   (YD_V7_STEPS - YD_WRAP) x 8 bytes.
   SPDX-License-Identifier: MIT */
#ifndef YSFDEC_H
#define YSFDEC_H
#include <stdint.h>

#define YD_SYNC_N 20
#define YD_FICH_SYMS 100
#define YD_PAY_SYMS 360
#define YD_FRAME_SYMS 480
#define YD_PAY_BITS 720
#define YD_INFO 280                 /* 5 x (48 + 8) */
#ifndef YD_WRAP
#define YD_WRAP 24                  /* the tail-biting decoder's run-in and run-out (fec.py: 48) */
#endif
#define YD_V7_STEPS (YD_INFO + 2 * YD_WRAP)
#define YD_NSF 5
#define YD_SF 6

/* the sync's levels; the correlator's weights are 5 x level - 3 (sum 0, so a DC offset drops
   out): sum w^2 = 2920, sum w x level = 584 */
static const int8_t YD_SYNC[YD_SYNC_N] = {-3, 3, 3, 1, 3, -3, 1, 3, -3, 1, -1, 3, 3, -1, 1, -3, 3, 1, -3, 3};
#define YD_W2 2920
#define YD_WL 584

/* n / d by shift and subtract (no divide on the M0, and clang turns a plain subtraction loop
   back into __aeabi_uidiv); the remainder in *r. n < 2^16 here */
__attribute__((noinline)) static uint32_t yd_div(uint32_t n, uint32_t d, uint32_t *r) {
  uint32_t q = 0, bit = 1;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  *r = n;
  return q;
}

/* FICH symbol s (0..99) is the code's dibit (s % 20) * 5 + s / 20 */
__attribute__((unused)) static uint32_t yd_fich_dibit(uint32_t s) {
  uint32_t r, q = yd_div(s, 20u, &r);
  return r * 5u + q;
}

__attribute__((unused)) static uint32_t yd_par(uint32_t v) {
  v ^= v >> 16, v ^= v >> 8, v ^= v >> 4, v ^= v >> 2, v ^= v >> 1;
  return v & 1u;
}

/* CRC-16 CCITT, init 0, inverted (ysf.py crc16) */
static const uint16_t YD_C16[16] = {0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7, 0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef};
static uint32_t yd_crc16_up(uint32_t c, const uint8_t *b, int n) {
  while (n--) {                         /* a nibble at a time */
    c ^= (uint32_t)*b++ << 8;
    c = ((c << 4) & 0xFFFFu) ^ YD_C16[(c >> 12) & 15u];
    c = ((c << 4) & 0xFFFFu) ^ YD_C16[(c >> 12) & 15u];
  }
  return c;
}
static uint32_t yd_crc16(const uint8_t *b, int n) { return yd_crc16_up(0, b, n) ^ 0xFFFFu; }

/* the FICH's K=5 decoder, step by step as the K=7 one: its 200 soft bits in code order (2 i,
   2 i + 1 = dibit i) */
typedef struct {
  int16_t pm[16];
  uint16_t dec[100];
  uint32_t step;
} yd_f5_t;

static void yd_f5_start(yd_f5_t *v) {
  uint32_t s;
  for (s = 0; s < 16; s++) v->pm[s] = s ? -8000 : 0;
  v->step = 0;
}

/* one step over llr (the 100 code dibits' soft bits, 4 bits each, -7..7, positive = 0: byte i
   holds dibit i, its first bit low); 1 when all 100 are done */
static int yd_f5_step(yd_f5_t *v, const uint8_t *llr) {
  uint32_t i = v->step, s, d = 0, q = llr[i];
  int32_t l0 = (int32_t)((q & 15u) ^ 8u) - 8, l1 = (int32_t)(((q >> 4) & 15u) ^ 8u) - 8, nm[16];
  /* butterflies: s = b << 3 | old >> 1, from old = (s & 7) << 1 | k (old: r0 = bit 3 .. r3 =
     bit 0); s and s | 8 both come from 2s and 2s + 1, and flipping b or k flips both outputs
     (the taps' first and last), so the branch metrics are m, -m, -m, m. No normalisation: a
     step adds at most 14, 100 of them and the start's -8000 fit an int16 */
  for (s = 0; s < 8; s++) {
    int32_t a = v->pm[2 * s], b = v->pm[2 * s + 1];
    int32_t m = ((s & 1u) ? -l0 : l0) + ((((s >> 2) ^ (s >> 1)) & 1u) ? -l1 : l1);
    int32_t x0 = a + m, y0 = b - m, x1 = a - m, y1 = b + m;
    if (y0 > x0) x0 = y0, d |= 1u << s;
    if (y1 > x1) x1 = y1, d |= 1u << (s + 8);
    nm[s] = x0, nm[s + 8] = x1;
  }
  for (s = 0; s < 16; s++) v->pm[s] = (int16_t)nm[s];
  v->dec[i] = (uint16_t)d;
  v->step = i + 1;
  return v->step >= 100;
}

/* after the last step: 1 and the 4 FICH bytes if the CRC holds (terminated: back from state 0;
   only the Golay words' data halves are taken) */
static int yd_f5_end(const yd_f5_t *v, uint8_t out[4]) {
  uint32_t i, s = 0;
  uint8_t w[6] = {0, 0, 0, 0, 0, 0};
  uint32_t g = 3, r = 23;                 /* data bit i of the 96: Golay word g = i / 24, bit
                                             r = i % 24 (counted down, no divide); its data
                                             half is bits 0-11 */
  for (i = 100; i-- > 0;) {
    if (i < 96) {
      if (r < 12 && (s >> 3)) {
        uint32_t k = g * 12u + r;           /* the 48-bit word's bit k (MSB first) */
        w[k >> 3] |= (uint8_t)(0x80u >> (k & 7u));
      }
      if (r-- == 0) r = 23, g--;
    }
    s = ((s & 7u) << 1) | ((v->dec[i] >> s) & 1u);
  }
  if (yd_crc16(w, 4) != ((uint32_t)w[4] << 8 | w[5])) return 0;
  for (i = 0; i < 4; i++) out[i] = w[i];
  return 1;
}

/* the PN9 (seed 0x1FF) that whitens the sotto VW payload: one bit a call */
__attribute__((unused)) static uint32_t yd_pn9(uint32_t *s) {
  uint32_t b = *s & 1u;
  *s = (*s >> 1) | ((((*s >> 4) ^ *s) & 1u) << 8);
  return b;
}

/* the code's info step i: its first coded bit c = 18 (i / 7) + {0,3,5,8,10,13,15}[i % 7] (g3
   kept only on even i % 7) is payload bit (c % 24) * 30 + c / 24. The decoder walks i in order
   (wrapping at 280), keeping i % 7 and c as 24 q + r: no divide a step */

typedef struct {
  int16_t pm[64];
  uint32_t dec[YD_V7_STEPS - YD_WRAP][2]; /* the first wrap's decisions: traceback stops short */
  uint32_t step;
  uint16_t t;                             /* the traceback's (0: not started) */
  uint8_t s;
  uint8_t i7, cr;                         /* the info step: i, i % 7, its first coded bit */
  uint16_t i, cq;
} yd_v7_t;

static void yd_v7_at(yd_v7_t *v, uint32_t i) {
  uint32_t r, q = yd_div(i, 7u, &r), c;
  static const uint8_t cum[7] = {0, 3, 5, 8, 10, 13, 15};
  v->i = (uint16_t)i, v->i7 = (uint8_t)r;
  c = q * 18u + cum[r];
  v->cq = (uint16_t)yd_div(c, 24u, &r), v->cr = (uint8_t)r;
}

static void yd_v7_start(yd_v7_t *v) {
  uint32_t s;
  for (s = 0; s < 64; s++) v->pm[s] = 0;
  v->step = 0, v->t = 0;
  yd_v7_at(v, YD_INFO - YD_WRAP);
}

/* one step of the wrap-around decoder over pay (the 720 descrambled soft bits, 4 bits each, two a
   byte, bit 2k in the low half: -7..7, positive = 0); 1 when all are
   done */
/* the outputs (o0 o1 o2 = bits 2 1 0) of the register (state s << 0, the older bit 6 clear) for
   g = 133, 171, 165 octal; with bit 6 set all three flip (each g has it) */
static const uint8_t YD_O7[32] = {
    0, 4, 1, 5, 6, 2, 7, 3, 7, 3, 6, 2, 1, 5, 0, 4, 3, 7, 2, 6, 5, 1, 4, 0, 4, 0, 5, 1, 2, 6, 3, 7};

static int yd_v7_step(yd_v7_t *v, const uint8_t *pay) {
  int32_t l[3], bm[8];
  uint32_t t = v->step, g, j, d0 = 0, d1 = 0, r = v->cr, q = v->cq;
  int16_t nm[64];
  l[2] = 0;
  for (g = 0; g < 3 - (v->i7 & 1u); g++, r++) {
    uint32_t p;
    if (r >= 24) r -= 24, q++;
    p = r * 30u + q;
    l[g] = (int32_t)(((pay[p >> 1] >> (4 * (p & 1))) & 15u) ^ 8u) - 8;
  }
  /* the next info step's first coded bit: 3 on (g3 kept) or 2 */
  r = v->cr + 3u - (v->i7 & 1u), q = v->cq;
  if (r >= 24) r -= 24, q++;
  if (++v->i7 == 7) v->i7 = 0;
  if (++v->i == YD_INFO) v->i = 0, v->i7 = 0, r = 0, q = 0;
  v->cr = (uint8_t)r, v->cq = (uint16_t)q;
  for (g = 0; g < 8; g++)                  /* outputs (o0 o1 o2) = bits 2 1 0 of g */
    bm[g] = ((g & 4u) ? -l[0] : l[0]) + ((g & 2u) ? -l[1] : l[1]) + ((g & 1u) ? -l[2] : l[2]);
  /* butterflies: states 2j and 2j+1 both come from j and j+32, and the four branches' outputs
     are o, ~o, ~o, o (every generator has its first and last taps), so their metrics are
     m, -m, -m, m. No normalisation: a step adds at most 21, and 328 of them fit an int16 */
  for (j = 0; j < 32; j++) {
    int32_t a = v->pm[j], b = v->pm[j + 32u], m = bm[YD_O7[j]];
    int32_t x0 = a + m, y0 = b - m, x1 = a - m, y1 = b + m;
    uint32_t dd = (uint32_t)(y0 > x0) | ((uint32_t)(y1 > x1) << 1);
    nm[2 * j] = (int16_t)(y0 > x0 ? y0 : x0);
    nm[2 * j + 1] = (int16_t)(y1 > x1 ? y1 : x1);
    if (j < 16) d0 |= dd << (2 * j);
    else d1 |= dd << (2 * j - 32);
  }
  for (j = 0; j < 64; j++) v->pm[j] = nm[j];
  if (t >= YD_WRAP) v->dec[t - YD_WRAP][0] = d0, v->dec[t - YD_WRAP][1] = d1;
  v->step = t + 1;
  return v->step >= YD_V7_STEPS;
}

/* the traceback, n steps a call (from the best end state, at the first); 1 when done. The info
   bits go in v->pm's room (no longer needed): 280 bits, MSB first */
static int yd_v7_tb(yd_v7_t *v, uint32_t n) {
  uint8_t *u = (uint8_t *)v->pm;
  uint32_t s = v->s, t = v->t;
  if (t == 0) {
    int16_t best = v->pm[0];
    for (s = 0, t = 1; t < 64; t++)
      if (v->pm[t] > best) best = v->pm[t], s = t;
    for (t = 0; t < 35; t++) u[t] = 0;
    t = YD_V7_STEPS;
  }
  for (; n && t > YD_WRAP; n--) {
    const uint32_t *dt = v->dec[--t - YD_WRAP];
    uint32_t d = (s < 32 ? dt[0] >> s : dt[1] >> (s - 32)) & 1u;
    if (t < YD_WRAP + YD_INFO && (s & 1u)) {
      uint32_t k = t - YD_WRAP;
      u[k >> 3] |= (uint8_t)(0x80u >> (k & 7u));
    }
    s = (s >> 1) | (d << 5);
  }
  v->s = (uint8_t)s, v->t = (uint16_t)t;
  return t == YD_WRAP;
}

/* after the traceback: the 280 info bits' middle copy -> out[30] (five superframes) and the
   CRC-8 flags (bit k: superframe k good) */
static uint32_t yd_v7_end(const yd_v7_t *v, uint8_t out[30]) {
  static const uint8_t C8[16] = {0, 7, 14, 9, 28, 27, 18, 21, 56, 63, 54, 49, 36, 35, 42, 45};
  const uint8_t *u = (const uint8_t *)v->pm;
  uint32_t k, i, ok = 0;
  /* 5 x 7 bytes: 6 data, CRC-8 (0x07, init 0, MSB first) */
  for (k = 0; k < YD_NSF; k++, u += 7) {
    uint32_t c = 0;
    for (i = 0; i < 6; i++) {
      out[6 * k + i] = u[i];
      c ^= u[i];
      c = ((c << 4) & 0xFFu) ^ C8[c >> 4];   /* a nibble at a time */
      c = ((c << 4) & 0xFFu) ^ C8[c >> 4];
    }
    if (c == u[6]) ok |= 1u << k;
  }
  return ok;
}

#endif
