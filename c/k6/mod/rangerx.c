/* rangerx: PHY R, the range mode's receiver (docs/k6-range.md, modem/k6r.py): coded two-level
   FFSK on the FD6818's plain FSK receiver, the chip's sensitive FM path, as a sign sampler.

   The sender (the IC-7100 from a wav, or the UV-380's own FFSK sender, c/common/ffsk_tx.c) sends
   tones of 600 Hz (bit 1) and 1200 Hz (bit 0) at 1200 baud, precoded (tone bit = line bit ^ the
   line bit before), so the audio's sign always changes at a bit's edge and its sign across that
   edge follows the line bit. The chip samples the sign of its discriminator's audio 4800 times a
   second (REG_58 00E9, REG_72 C195: four samples a bit), de-emphasis and the rest of the AF path
   as found, the AFC on and no tune unless asked (the POCSAG bench: a tune restarts the AFC and
   the samples are noisy for a minute or two after).

   Reading: the chip's packet starts on the sync word F0F0, which the burst's 600 Hz lead and gaps
   give every two bits, and the FIFO is followed word by word as pocsag.c does (bench run 4's fix,
   PR #74: an empty FIFO's read gives the word of eight before; words go on eight behind).

   Slicing (modem.k6r.Slicer, step for step): times in Q8 samples, sample i centred at 256 i + 128;
   the bit edge e and the bit's length t (4 samples, learnt within 4 +- 0.62). A line bit's soft
   value y = (samples centred in the half bit before the next edge) - (those in the half bit
   after it), at most +-5. The same window one sample early and one late steers the clock: the
   edge moves by KP (|late| - |early|), t by the running sum of those over 2^KI_SHIFT. Every
   HALF_WIN bits the window half a bit on is compared, and when its |y| sum beats the clock's by
   HALF_MARGIN the clock took the bits' middles for their edges and moves half a bit (not during a
   packet).

   Packets: the sync, 32 line bits 1ACFFC1D, is a correlation of the last 32 soft values with
   it, |c| >= SYNC_MIN (of 128 at most), whose sign says which way up the burst is. The 2064 soft
   values after it are kept (4 bits each, -7..7), de-interleaved (24 rows of 86: line bit i is
   coded bit 86 (i % 24) + i / 24), and decoded by a K=7 Viterbi (133, 171, as PHY G, c/k6/ifpager.c)
   in slices between the FIFO's reads; the 128 bytes are a K6 pager XL payload, whitened, its CRC
   checked. As soon as a packet's last soft value is in the chip is re-armed for the next gap.

   Lines:
     range start hz=440850000 tune=0 sm=60 s=600
     r 3 t=12345 c=-118 y=31 e=41 ok=1 s=2 r=-118 k=1024
                    a packet: its number, ms at its sync, the sync's correlation (its sign: the
                    burst's way up), the mean |soft value| x 10 (4 bits a soft value: 40 at most
                    from a clean four samples), the line bits whose sign the decoded packet's own
                    code disagrees with (the channel's errors, out of 2064), CRC right, seq (header
                    bytes 2-3, low 13 bits; meaningless unless ok=1), RSSI peak, bit length (Q8
                    samples)
     range end n=12 ok=11 c=30 z=18 f=2 l=0 m=-124
                    packets, CRC right, chip packets started, of those with no sync, half-bit
                    moves, packets whose decode was not done when the next one came, RSSI min
   Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 2B, 3F and 02, put back as read. Receive only.
   Returns the packets that checked, or -3 (old image), -8 (scratch).
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 2
#endif
#ifndef K6_MODULE_OPT
#define K6_MODULE_OPT z
#endif
#include "../k6_mod.h"

#ifndef RG_S
#define RG_S 600u        /* s the module listens */
#endif
#ifndef WAIT_MS
#define WAIT_MS 0u
#endif
#ifndef RG_TUNE
#define RG_TUNE 0        /* 0: as found (no tune); 1: tune rx_hz narrow, 2: wide */
#endif
#ifndef SYNC_MIN
#define SYNC_MIN 56
#endif
#define RESYNC_BITS 160u /* a packet's first soft values: a sync better by RESYNC_MARGIN than the */
#define RESYNC_MARGIN 16 /* one it started on starts it again there */
#ifndef SLOT_SCALE
#define SLOT_SCALE 1000u /* ms a unit of RG_S (the emulated test shortens it) */
#endif

#define RG_SYNC 0x1ACFFC1Du
#define CODED 2064u
#define STEPS 1032u
#define NROW 24u
#define NCOL 86u
#define Q_MAX 7
#define T0 1024
#define KP 8
#define KI_SHIFT 4
#define T_LIM 160
#define HALF_WIN 32
#define HALF_MARGIN 24
#define TB_RING 96
#define TB_OUT 32
#define VIT_SLICE 64     /* trellis steps between FIFO reads */

/* the chip: plain receiver, 4800 a second, a packet of 2048 bytes, sync word F0F0 */
#define CFG 0x00E9u
#define B72 49557u
#define LEN 0xFFE0u
#define SYNW 0xF0F0u
#define WT 533u          /* a word in 1/16 ticks of 100 us */
#define NWORDS 1024u
#define END_W 10u
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u

#ifdef RG_LADDER
static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x13, 0x7E};
#define SAVED_13 10
#define SAVED_7E 11
#ifndef LADDER_S
#define LADDER_S 45u     /* s a setting */
#endif
/* the ladder: front end path (0 the one api->tune picked, 1 the other, 2 neither: fesw.c, about
   48 and 52 dB of loss, docs/k6-fesw.md) and REG_13 (0: the AGC as found; else the gain fixed
   there, REG_7E bit 15 with index 3 as surf.c: about -35, -40, -43, -51, -60 dB by egzumer's
   table, docs/k6-dmr.md); the first setting again last (the POCSAG bench: the first minutes after
   a module starts are not like the rest) */
static const uint8_t LPATH[] = {0, 1, 2, 2, 2, 2, 2, 2, 0};
static const uint16_t LG13[] = {0, 0, 0, 0x021Fu, 0x011Fu, 0x001Fu, 0x0007u, 0x0004u, 0};
#define NL sizeof(LPATH)
#define GPIOA_ODR 0x48000014u
#define GPIOA_BSRR 0x48000018u
#define GPIOA_BRR 0x48000028u
#define PINS (3u << 13)  /* PA13 the UHF path, PA14 the VHF path */
static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }
/* the pins to `want` (one of them, or none): clear first, so never both */
static void pins(uint32_t want) {
  wr(GPIOA_BRR, PINS & ~want);
  if (want) wr(GPIOA_BSRR, want);
}
#else
static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F};
#endif
#define NSV sizeof(SAVED)
#define SAVED_2B 8

/* the code's outputs for new state 2j with the older predecessor bit 0 (c/k6/ifpager.c) */
static const uint8_t COUT[32] = {0, 2, 0, 2, 3, 1, 3, 1, 3, 1, 3, 1, 0, 2, 0, 2,
                                 1, 3, 1, 3, 2, 0, 2, 0, 2, 0, 2, 0, 1, 3, 1, 3};

typedef struct {
  uint16_t sv[NSV];
  /* following the chip's FIFO (pocsag.c's follower) */
  uint16_t ring[8], alt[8];
  uint32_t due, per, tp, pt, lmn, lmt;
  uint16_t fv, cv, cs;
  uint8_t altm, fl, ck, over;
  uint32_t el;
  uint16_t tl;
  uint32_t rn;
  /* the slicer */
  uint32_t n;
  int8_t hist[16];
  int32_t e, t, acc, son, shalf;
  uint8_t nb, hold, inpk, dpend;
  /* the sync hunt and the packet */
  int8_t yr[32];
  uint8_t yi, ny;
  int8_t pol, capsync;
  uint32_t ni, ysum;
  uint8_t q[CODED / 2];
  /* the decoder */
  int16_t pm[2][64];
  uint32_t dec[TB_RING][2];
  uint32_t ls, out, ri, cur;
  uint16_t rc[2]; /* the next coded bit's row and column */
  uint8_t pkt[128];
  /* the packet's numbers, and the run's */
  int32_t pc, pcs; /* the sync's |correlation|, and signed */
  uint32_t pt0, prssi, rssi, rmin, resync;
  int32_t lc;      /* the decoded packet's, for its line: sync, ms, RSSI, sum |soft|, bit length */
  uint32_t lt0, lrssi, lys, lk;
  uint32_t np, nok, ncap, nz, flips, late;
#ifdef RG_LADDER
  uint32_t own, lcur, tset, vnp, vok, vlo, vhi; /* the path tune picked; the setting, its start, its
                                                    packets, those right, RSSI low and high */
#endif
#ifdef RG_DUMP
#ifndef DUMP_CAPS
#define DUMP_CAPS 6u
#endif
  uint16_t dk[RG_DUMP]; /* the capture's words as they went to the slicer (rangerx_d) */
  uint32_t dn, nd, dt, af, dset; /* af: REG_2B's bits set (the second half: 0100, de-emphasis off) */
#endif
  char ln[64];
} st_t;

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static uint32_t udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
  if (!d) return 0;
  while (d < n && !(d & 0x80000000u)) d <<= 1, bit <<= 1;
  while (bit) {
    if (n >= d) n -= d, q |= bit;
    d >>= 1, bit >>= 1;
  }
  return q;
}

static char *dec(char *p, uint32_t v) {
  char t[10];
  int n = 0;
  do {
    uint32_t q = udiv(v, 10);
    t[n++] = (char)('0' + v - 10 * q);
    v = q;
  } while (v);
  while (n) *p++ = t[--n];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v); }

static char *kvs(char *p, const char *k, int32_t v) {
  p = put(p, k);
  if (v < 0) *p++ = '-', v = -v;
  return dec(p, (uint32_t)v);
}

static void say(const k6_api_t *api, st_t *s, char *end) {
  *end = 0;
  api->say(s->ln);
}

static int32_t iabs(int32_t v) { return v < 0 ? -v : v; }

static uint32_t swap(uint32_t w) { return (w & 0xFF) << 8 | w >> 8; }

/* ---- the decoder ---- */

/* a kept soft value, line bit i */
static int32_t qget(const st_t *s, uint32_t i) {
  uint32_t b = s->q[i >> 1];
  if (i & 1) b >>= 4;
  return (int32_t)(b << 28) >> 28;
}

static void qput(st_t *s, uint32_t i, int32_t v) {
  uint8_t *b = &s->q[i >> 1];
  if (i & 1) *b = (uint8_t)((*b & 0x0F) | (uint32_t)(v & 15) << 4);
  else *b = (uint8_t)((*b & 0xF0) | (uint32_t)(v & 15));
}

/* the line bit of the next coded bit (j = 86 row + col: 24 col + row), and on to the one after */
static uint32_t line_next(uint16_t *rc) {
  uint32_t l = (uint32_t)rc[1] * NROW + rc[0];
  if (++rc[1] == NCOL) rc[1] = 0, rc[0]++;
  return l;
}

static void traceback(st_t *s, uint32_t st, uint32_t r, uint32_t end, uint32_t from, uint32_t to) {
  uint32_t k = end;
  if (to > 1024) to = 1024;
  while (k-- > from) {
    uint32_t c;
    if (k < to && (st & 1)) s->pkt[k >> 3] |= (uint8_t)(0x80u >> (k & 7));
    c = (s->dec[r][st >> 5] >> (st & 31)) & 1;
    st = (st >> 1) | (c << 5);
    r = r ? r - 1 : TB_RING - 1;
  }
}

static void vit_start(st_t *s) {
  uint32_t k;
  s->ls = 0, s->out = 0, s->ri = 0, s->cur = 0, s->rc[0] = s->rc[1] = 0;
  for (k = 0; k < 32; k++) ((uint32_t *)s->pkt)[k] = 0;
  for (k = 0; k < 64; k++) s->pm[0][k] = k ? -8000 : 0;
}

/* one trellis step; -> 1 when the packet is decoded */
static int vit_step(st_t *s) {
  int16_t *a = s->pm[s->cur], *b = s->pm[s->cur ^ 1];
  int32_t l0 = qget(s, line_next(s->rc)), l1 = qget(s, line_next(s->rc)), bm[4], base = a[0];
  uint32_t d[2] = {0, 0}, j;
  bm[0] = l0 + l1, bm[1] = l0 - l1, bm[2] = l1 - l0, bm[3] = -l0 - l1;
  for (j = 0; j < 32; j++) {
    int32_t m = bm[COUT[j]], x = a[j] - base, y = a[j + 32] - base, u, v;
    uint32_t bit = 1u << ((2 * j) & 31), *w = &d[j >> 4];
    u = x + m, v = y - m;
    if (v > u) b[2 * j] = (int16_t)v, *w |= bit;
    else b[2 * j] = (int16_t)u;
    u = x - m, v = y + m;
    if (v > u) b[2 * j + 1] = (int16_t)v, *w |= bit << 1;
    else b[2 * j + 1] = (int16_t)u;
  }
  s->cur ^= 1;
  s->dec[s->ri][0] = d[0], s->dec[s->ri][1] = d[1];
  s->ls++;
  if (s->ls == STEPS) {
    traceback(s, 0, s->ri, s->ls, s->out, s->ls); /* the tail brought it to state 0 */
    return 1;
  }
  if (!(s->ls & (TB_OUT - 1)) && s->ls >= TB_RING) {
    uint32_t st = 0, k;
    for (k = 1; k < 64; k++)
      if (b[k] > b[st]) st = k;
    traceback(s, st, s->ri, s->ls, s->out, s->out + TB_OUT);
    s->out += TB_OUT;
  }
  s->ri = s->ri == TB_RING - 1 ? 0 : s->ri + 1;
  return 0;
}

static uint32_t parity(uint32_t v) {
  v ^= v >> 4, v ^= v >> 2, v ^= v >> 1;
  return v & 1;
}

/* the decoded packet: its CRC, seq, and the line bits its code disagrees with */
static void pkt_line(const k6_api_t *api, st_t *s) {
  uint32_t i, k, reg = 0, err = 0, crc = 0xFFFF, w = 0x1FF, h[4] = {0, 0, 0, 0}, rx = 0, okc;
  uint16_t rc[2] = {0, 0};
  char *p;
  for (i = 0; i < STEPS; i++) {
    uint32_t bit = i < 1024 ? (uint32_t)(s->pkt[i >> 3] >> (7 - (i & 7))) & 1 : 0, c;
    reg = ((reg << 1) | bit) & 127;
    for (k = 0; k < 2; k++) {
      int32_t v = qget(s, line_next(rc));
      c = parity(reg & (k ? 0171u : 0133u));
      if (c ? v >= 0 : v <= 0) err++;
    }
  }
  for (i = 0; i < 128; i++) {
    uint32_t b = s->pkt[i] ^ (w & 0xFF);
    for (k = 0; k < 8; k++) w = (w >> 1) | (((w ^ (w >> 5)) & 1) << 8);
    if (i < 4) h[i] = b;
    if (i < 126) {
      crc ^= b << 8;
      for (k = 0; k < 8; k++) crc = crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1;
      crc &= 0xFFFF;
    } else {
      rx = rx << 8 | b;
    }
  }
  okc = crc == rx;
  s->nok += okc;
#ifdef RG_LADDER
  s->vnp++, s->vok += okc;
#endif
  p = kv(s->ln, "r ", ++s->np);
  p = kv(p, " t=", s->lt0);
  p = kvs(p, " c=", s->lc);
  p = kv(p, " y=", (s->lys * 1270u) >> 18);
  p = kv(p, " e=", err);
  p = kv(p, " ok=", okc);
  p = kv(p, " s=", (h[2] << 8 | h[3]) & 0x1FFF);
  p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(s->lrssi));
  p = kv(p, " k=", s->lk);
  say(api, s, p);
}

/* ---- the slicer and the hunt ---- */

static void soft(st_t *s, const k6_api_t *api, int32_t y) {
  uint32_t i;
  int32_t c = 0;
  s->yr[s->yi] = (int8_t)y;
  s->yi = (s->yi + 1) & 31;
  if (s->ny < 32) s->ny++;
  if (s->ny == 32 && (!s->inpk || s->ni < RESYNC_BITS))
    for (i = 0; i < 32; i++) {
      int32_t v = s->yr[(s->yi + i) & 31];
      c += (RG_SYNC >> (31 - i)) & 1 ? -v : v;
    }
  if (s->inpk && s->ni < RESYNC_BITS && iabs(c) >= SYNC_MIN && iabs(c) >= s->pc + RESYNC_MARGIN) {
    /* a better sync early in the packet: it starts again there */
    s->pol = (int8_t)(c > 0 ? 1 : -1), s->pc = iabs(c), s->ni = 0, s->ysum = 0, s->resync++;
    s->pt0 = api->ms(), s->pcs = c;
    return;
  }
  if (s->inpk) {
    int32_t v = s->pol * y;
    if (!s->ni && s->dpend) {
      /* the last packet's decode or line is not done: both now, before its soft values go */
      if (s->dpend == 1) {
        while (!vit_step(s)) {}
        s->late++;
      }
      pkt_line(api, s);
      s->dpend = 0;
    }
    if (v > Q_MAX) v = Q_MAX;
    if (v < -Q_MAX) v = -Q_MAX;
    qput(s, s->ni, v);
    s->ysum += (uint32_t)iabs(v);
    if (++s->ni == CODED) {
      s->inpk = 0, s->hold = 0, s->ny = 0;
      s->lc = s->pcs, s->lt0 = s->pt0, s->lrssi = s->prssi, s->lys = s->ysum, s->lk = (uint32_t)s->t;
      vit_start(s);
      s->dpend = 1, s->over = 1;
    }
    return;
  }
  if (iabs(c) >= SYNC_MIN) {
    s->inpk = 1, s->pol = (int8_t)(c > 0 ? 1 : -1), s->hold = 1, s->ni = 0, s->ysum = 0;
    s->pc = iabs(c), s->pcs = c, s->pt0 = api->ms(), s->prssi = s->rssi, s->capsync = 1;
  }
}

/* sum over samples centred in [a, a + t/2) minus those in [a + t/2, a + t) */
static int32_t win(const st_t *s, int32_t a) {
  int32_t h = s->t >> 1, y = 0;
  uint32_t i = s->n > 16 ? s->n - 16 : 0;
  for (; i < s->n; i++) {
    int32_t c = (int32_t)(i << 8) + 128;
    if (c < a) continue;
    if (c < a + h) y += s->hist[i & 15];
    else if (c < a + s->t) y -= s->hist[i & 15];
    else break;
  }
  return y;
}

static void smp(st_t *s, const k6_api_t *api, uint32_t b) {
  s->hist[s->n & 15] = (int8_t)(b ? 1 : -1);
  s->n++;
  for (;;) {
    int32_t t = s->t, a = s->e + (t >> 1), y, el, lim = T_LIM << KI_SHIFT;
    if ((int32_t)(s->n << 8) < a + t + 384) break;
    y = win(s, a);
    el = iabs(win(s, a + 256)) - iabs(win(s, a - 256));
    s->son += iabs(y);
    s->shalf += iabs(win(s, a + (t >> 1)));
    if (++s->nb >= HALF_WIN) {
      if (s->shalf > s->son + HALF_MARGIN && !s->hold) s->e += t >> 1, s->flips++;
      s->nb = 0, s->son = 0, s->shalf = 0;
    }
    s->e += t + KP * el;
    s->acc += el;
    if (s->acc > lim) s->acc = lim;
    if (s->acc < -lim) s->acc = -lim;
    s->t = T0 + (s->acc >> KI_SHIFT);
    soft(s, api, y);
  }
}

/* a word of the chip's (16 samples, the first at the top); -> 1: the packet is in, re-arm */
static int word_in(st_t *s, const k6_api_t *api, uint32_t v) {
  uint32_t i;
#ifdef RG_DUMP
  if (s->nd < DUMP_CAPS && s->dn < RG_DUMP) s->dk[s->dn++] = (uint16_t)v;
#endif
  for (i = 16; i--;) smp(s, api, v >> i & 1);
  return s->over;
}

#ifdef RG_DUMP
static char *hex4(char *p, uint32_t v) {
  uint32_t i;
  for (i = 4; i--;) {
    uint32_t d = (v >> (4 * i)) & 15u;
    *p++ = (char)(d < 10 ? '0' + d : 'a' + d - 10);
  }
  *p = 0;
  return p;
}

/* the capture's words, for the host (python3 -m modem.k6r dump LOG): a c line, then d lines of
   twelve words, the first sample at the top of each */
static void dump_say(const k6_api_t *api, st_t *s) {
  uint32_t i, k = 0;
  char *p;
  if (!s->dn || s->nd >= DUMP_CAPS) return;
  s->nd++;
  p = kv(s->ln, "c ", s->ncap);
  p = kv(p, " t=", s->dt);
  p = kv(p, " w=", s->dn);
  p = kv(p, " k=", (uint32_t)s->t);
  say(api, s, p);
  for (i = 0; i < s->dn; i++) {
    if (!k) {
      p = kv(s->ln, "d ", s->ncap);
      *p++ = ' ';
      p = hex4(p, i);
      *p++ = ' ';
    }
    p = hex4(p, s->dk[i]);
    if (++k == 12) say(api, s, p), k = 0;
  }
  if (k) say(api, s, p);
  s->dn = 0;
}
#endif

/* ---- following the chip (pocsag.c) ---- */

static uint32_t clk(const k6_api_t *api, st_t *s) {
  uint16_t t = (uint16_t)api->ticks();
  s->el += (uint16_t)(t - s->tl);
  s->tl = t;
  return s->el;
}

static int take(st_t *s, const k6_api_t *api, uint32_t v, int seen, uint32_t when) {
  uint32_t k = s->rn & 7, n;
  int over = s->rn >= 8 ? word_in(s, api, s->ring[k]) : 0;
  s->ring[k] = (uint16_t)v, s->altm &= (uint8_t)~(1u << k), s->rn++;
  if (seen) {
    int32_t e = (int32_t)(when - s->due);
    if (e > (int32_t)(s->per >> 2) || -e > (int32_t)(s->per >> 2)) s->due = when;
    else s->due += (uint32_t)(e / 2);
    n = s->rn - 1 - s->lmn;
    if (n >= 16) {
      if (n <= 64) s->per = (s->per + udiv(s->due - s->lmt, n)) >> 1;
      s->lmn = s->rn - 1, s->lmt = s->due;
    }
  }
  s->due += s->per;
  return over;
}

static int fol(st_t *s, const k6_api_t *api, uint32_t v, uint32_t now) {
  uint32_t k, w, q = s->per * 45 >> 7;
  int over = 0;
  if (s->fl & 4) {
    s->fl &= 3;
    if (v == s->cv) s->ring[s->ck] = s->cs, s->alt[s->ck] = s->cv, s->altm |= (uint8_t)(1u << s->ck);
  }
  if (s->fl & 2) {
    s->fl &= 5;
    if (v == s->fv) {
      s->ring[s->rn & 7] = (uint16_t)v, s->altm &= (uint8_t)~(1u << (s->rn & 7));
      s->fl |= 1, s->tp = now;
      return 0;
    }
    over = take(s, api, s->fv, (s->fl & 1) && s->pt - s->tp < (s->per * 51 >> 8), s->tp + (s->pt - s->tp) / 2);
    s->fl &= 6;
    if (over) return over;
  }
  k = s->rn & 7;
  w = s->ring[k];
  if (s->altm >> k & 1) {
    if (v == w) s->altm &= (uint8_t)~(1u << k);
    else if (v == s->alt[k]) {
      s->ring[k] = (uint16_t)v, s->altm &= (uint8_t)~(1u << k);
      s->fl |= 1, s->tp = now;
      return 0;
    }
  }
  if (v != w) {
    int st = s->fl & 1;
    if ((int32_t)(now - s->due + q) < 0) {
      s->fl |= 2, s->fv = (uint16_t)v, s->pt = now;
      return 0;
    }
    over = take(s, api, v, st && now - s->tp < (s->per * 51 >> 8), s->tp + (now - s->tp) / 2);
    s->fl &= 6;
    if (st && s->rn > 8) s->fl |= 4, s->ck = (uint8_t)k, s->cv = (uint16_t)v, s->cs = (uint16_t)w;
    return over;
  }
  if ((int32_t)(now - s->due - q) >= 0) {
    s->fl &= 6;
    return take(s, api, v, 0, 0);
  }
  s->fl |= 1, s->tp = now;
  return 0;
}

/* the modem off and on with the sync word, armed twice (pocsag.c's seek); -> the empty FIFO's
   word */
static uint32_t seek(const k6_api_t *api, const st_t *s) {
  uint32_t i, w = 0, ctrl = 0x00F0u;
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
#ifdef RG_DUMP
  api->fd_write(0x2B, (uint16_t)(s->sv[SAVED_2B] | s->af));
#else
  api->fd_write(0x2B, s->sv[SAVED_2B]);
#endif
  api->fd_write(0x5A, SYNW);
  api->fd_write(0x5B, SYNW);
  api->fd_write(0x58, CFG);
  api->fd_write(0x72, B72);
  api->fd_write(0x5C, 0x5625);
  api->fd_write(0x5D, LEN);
  api->fd_write(0x5E, 0x0206);
  for (i = 0; i < 2; i++) {
    api->fd_write(0x59, (uint16_t)(ctrl | CTRL_CLR));
    api->fd_write(0x59, (uint16_t)(ctrl | CTRL_EN));
    api->fd_write(0x02, 0);
    if (!i) api->delay_us(2000);
  }
  for (i = 0; i < 8; i++) w = api->fd_read(0x5F);
  return swap(w);
}

#ifdef RG_LADDER
/* the ladder's setting cur: its path and gain, and its line */
static void lset(const k6_api_t *api, st_t *s, uint32_t now) {
  uint32_t c = s->lcur;
  char *p;
  pins(LPATH[c] == 0 ? s->own : LPATH[c] == 1 ? s->own ^ PINS : 0);
  if (LG13[c]) {
    api->fd_write(0x13, LG13[c]);
    api->fd_write(0x7E, (uint16_t)((s->sv[SAVED_7E] & 0x0FFFu) | 0xB000u));
  } else {
    api->fd_write(0x7E, s->sv[SAVED_7E]);
    api->fd_write(0x13, s->sv[SAVED_13]);
  }
  s->tset = now, s->vnp = s->vok = 0, s->vlo = 0x1FF, s->vhi = 0;
  p = kv(s->ln, "s ", c);
  p = kv(p, " t=", now);
  p = kv(p, " p=", LPATH[c]);
  p = kv(p, " g=", LG13[c]);
  say(api, s, p);
}

/* the setting's end: its packets, those right, RSSI low and high */
static void lend(const k6_api_t *api, st_t *s) {
  char *p = kv(s->ln, "v ", s->lcur);
  p = kv(p, " n=", s->vnp);
  p = kv(p, " ok=", s->vok);
  p = kv(p, " lo=-", (uint32_t)-KM_RSSI_DBM(s->vlo));
  p = kv(p, " hi=-", (uint32_t)-KM_RSSI_DBM(s->vhi));
  say(api, s, p);
}
#endif

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now, tkey = 0, prev = 0, t0 = 0;
#ifndef RG_LADDER
  uint32_t tend;
#endif
  int key = 0, cap = 0, armed = 0;
  char *p;
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(st_t) / 4; i++) ((uint32_t *)s)[i] = 0;
  for (i = 0; i < NSV; i++) s->sv[i] = api->fd_read(SAVED[i]);
  s->t = T0, s->rmin = 0x1FF;
  if (RG_TUNE) api->tune(api->rx_hz(), RG_TUNE == 2);
#ifdef RG_LADDER
  s->own = rd(GPIOA_ODR) & PINS;
  if (s->own != (1u << 13) && s->own != (1u << 14)) {
    p = kv(s->ln, "range pins=", s->own);
    say(api, s, p);
    return -5; /* nothing written: not the board this was made for */
  }
#endif
  s->tl = (uint16_t)api->ticks();
  p = kv(s->ln, "range start hz=", api->rx_hz());
  p = kv(p, " tune=", RG_TUNE);
  p = kv(p, " sm=", SYNC_MIN);
  p = kv(p, " s=", RG_S);
  say(api, s, p);
  while (api->ms() < WAIT_MS && !key) {
    api->delay_us(50000);
    key = api->level >= 4 ? api->key_poll() : api->key_down();
  }
#ifdef RG_LADDER
  lset(api, s, api->ms());
#else
  tend = api->ms() + RG_S * SLOT_SCALE;
#endif
#ifdef RG_DUMP
  p = kv(s->ln, "s 0 t=", api->ms());
  p = put(p, " p=0 g=0 a=0");
  say(api, s, p);
#endif
  while (!key) {
    now = api->ms();
    clk(api, s);
    if (now - tkey >= 50) {
      uint32_t r = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      s->rssi = r;
      if (r < s->rmin) s->rmin = r;
      if (s->inpk && r > s->prssi) s->prssi = r;
#ifdef RG_LADDER
      if (r < s->vlo) s->vlo = r;
      if (r > s->vhi) s->vhi = r;
#endif
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key) break;
    }
#ifdef RG_LADDER
    if (!s->inpk && !s->dpend && now - s->tset >= LADDER_S * SLOT_SCALE) {
      lend(api, s);
      if (++s->lcur >= NL) break;
      lset(api, s, now);
    }
#else
    if (!cap && (int32_t)(now - tend) >= 0 && !s->inpk && !s->dpend) break;
#endif
#ifdef RG_DUMP
    /* the second half with the AF path's de-emphasis off (REG_2B bit 8, as pocsag.c's 512 baud),
       its first captures said too */
    if (!s->dset && !cap && !s->inpk && !s->dpend && (int32_t)(now - tend + RG_S * SLOT_SCALE / 2) >= 0) {
      char *q;
      s->dset = 1, s->af = 0x0100u, s->nd = 0, armed = 0;
      q = kv(s->ln, "s 1 t=", now);
      q = kv(q, " p=0 g=0 a=", s->af);
      say(api, s, q);
    }
#endif
    if (!cap) {
      uint32_t v;
      if (!armed) {
        prev = seek(api, s);
        armed = 1;
      }
      v = swap(api->fd_read(0x5F));
      if (v != prev) {
        /* the chip's packet has begun (on F0F0: a bit's edge just before its first sample) */
        cap = 1, t0 = clk(api, s) << 4;
        s->ncap++, s->capsync = 0, s->over = 0;
#ifdef RG_DUMP
        s->dn = 0, s->dt = now;
#endif
        s->n = 0, s->e = 0, s->nb = 0, s->son = s->shalf = 0, s->hold = 0, s->inpk = 0, s->ny = 0;
        for (i = 0; i < 8; i++) s->ring[i] = (uint16_t)prev;
        s->ring[0] = (uint16_t)v, s->rn = 1, s->altm = 0, s->fl = 0;
        s->per = (uint32_t)WT << 4, s->lmn = 0, s->lmt = t0 << 4, s->due = s->lmt + s->per;
      }
    }
    while (cap) {
      int over = fol(s, api, swap(api->fd_read(0x5F)), clk(api, s) << 8);
      if (over || s->rn >= NWORDS ||
          (int32_t)((clk(api, s) << 4) - t0 - (NWORDS + NWORDS / 16 + END_W) * WT) >= 0) {
        /* re-armed at once for the next packet; a packet cut off by the chip is dropped */
        for (i = s->rn > 8 ? s->rn - 8 : 0; i < s->rn && !over && s->inpk; i++)
          over = word_in(s, api, s->ring[i & 7]);
        if (s->inpk) s->inpk = 0, s->hold = 0;
        if (!s->capsync) s->nz++;
        cap = 0, armed = 0;
        api->fd_write(0x59, 0);
#ifdef RG_DUMP
        dump_say(api, s);
#endif
      } else if ((s->fl & 7) == 1) {
        break; /* the empty FIFO: on again after a pause */
      }
    }
    if (s->dpend == 1) {
      for (i = 0; i < VIT_SLICE && s->dpend == 1; i++)
        if (vit_step(s)) s->dpend = 2;
    } else if (s->dpend == 2) {
      pkt_line(api, s);
      s->dpend = 0;
    }
    api->delay_us(300);
  }
#ifdef RG_LADDER
  pins(s->own);
#endif
  for (i = 0; i < NSV; i++) api->fd_write(SAVED[i], s->sv[i]);
  api->fd_write(0x02, 0);
  p = kv(s->ln, "range end n=", s->np);
  p = kv(p, " ok=", s->nok);
  p = kv(p, " c=", s->ncap);
  p = kv(p, " z=", s->nz);
  p = kv(p, " f=", s->flips);
  p = kv(p, " l=", s->late);
  p = kv(p, " x=", s->resync);
  p = kv(p, " m=-", (uint32_t)-KM_RSSI_DBM(s->rmin));
  say(api, s, p);
  return (int)s->nok;
}
