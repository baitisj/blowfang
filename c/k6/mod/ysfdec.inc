/* ysfdec.inc: YSF frames off dmrsync.h's grid, for dmrdet.c's YSF_DECODE build (c/k6/mod/ysfrx.c,
   docs/k6-c4fm.md "Receiving C4FM sotto") and the host check (c/k6/ysfrx_host.c). Receive only.
   The grid's matched filter (f[], 2 points a symbol) is searched for the YSF sync (20 symbols,
   c/k6/ysfdec.h); a frame found is sliced symbol by symbol as its points come in, at the sync's
   own scale and DC; the next frame's sync is looked for where it is due (480 symbols on) and
   taken even when weak (the flywheel). The FICH and the sotto VW payload are decoded step by
   step in the gaps between the DMA's samples (yx_work). A comms frame's five sotto 1200
   superframes go to their places in the message (c/common/sotto_rx.c: frame number x 5 + k,
   so a lost frame or a superframe failing its CRC-8 leaves a hole), and when the call ends (a
   terminator, or two frames that aren't there) the holes are filled with the superframe before
   (srx_conceal) and housekeeping's lines give the message:
     Y call fr=24 fich=24 vw=110/110 sf=110 miss=0 crc=3c1f
        frames, FICHs good, superframes good / read, the message's superframes and those filled
        in (over=: superframes past the message buffer), the CRC-16 of the message
     Y d 0 000102030405...                      the message, 24 bytes a line
   The message buffer is what the module's scratch has left (yx_mem): 6 bytes a superframe
   (40 ms of sotto 1200) and a bit.
   SPDX-License-Identifier: MIT */

#define SRX_NO_RADIO
#include "../../common/sotto_rx.c"
#ifndef YX_MSG_MIN
#define YX_MSG_MIN 64u          /* the module runs with a message buffer of 2.6 s or more */
#endif
#define YX_THR_Q8 166u          /* rho^2 0.65: a sync */
#define YX_FLY_Q8 40u           /* rho^2 0.16: a due sync taken where it is best */
#define YX_K 32                 /* soft bits: 32 a level step */
#ifdef YX_TRACE
#define YX_TR 24                /* frames traced a call */
#endif
#define YX_WIDE 128             /* the first next sync's search: +-128 quarter points (3.3% of a
                                   frame), every grid point, then the quarter points by the best */
#define YX_LOCKW 6              /* once timed: +-6 quarter points */

typedef struct {
  /* the search: the next grid point to try; the candidate (cand 1, 2 while its quarter points
     are tried) */
  uint32_t g, cg, cr2;
  int cand;
  /* the best of a few quarter points tried, one a call (around a candidate, or where the next
     sync is due): the next offset, the best's rho^2, place, correlation and sum */
  int32_t rq, rc, rs;
  uint32_t rr, rP;
  /* the symbol clock: on the radio the grid's rate (the DMA's, by the MCU's clock) was 1.4% off
     the signal's (bench run 6, 2026-11-19), and 480 symbols on the next sync was ~7 symbols from
     where it was due, past the +-3 quarter points tried. Until a frame's syncs have timed the
     grid (rlock) the next sync is looked for +-YX_WIDE (rf: the fine tries' turn, rend: their
     last); then each sync moves the grid's step 1/8 of the way, as DMR's slots do (dx_time).
     Each call times it again (its first frame's sync, wide), and a missed sync looks wide again:
     on the radio (bench run 12) a step kept from an earlier call held the next syncs outside
     +-3 and calls fell apart after a frame or two. timed: a sync has timed it (the DMA's
     measured rate no longer sets it, dmrdet.c) */
  int32_t rend;
  uint8_t rlock, rf, timed;
  /* the frame: its sync's last symbol (19) at P0 quarter grid points; the next symbol; the
     scale (k16: soft units a filter unit, Q16), the DC, the polarity */
  int on;
  uint32_t P0, n, pn;
  int32_t k16, u, dc, inv;
  uint8_t fich[YD_FICH_SYMS];          /* the code's dibits' soft bits, 4 bits each */
  /* the payload's soft bits, 4 bits each (yd_v7_step), two buffers: one frame's decodes while
     the next one's comes in (the 25 ms between a payload's end and the next one's start is too
     short for the K=7 decoder in the DMA's gaps: emulated, 250 of its 330 steps had to be done at
     once, and the samples lost meanwhile cost most of the frames) */
  uint8_t pay[2][YD_PAY_BITS / 2];
  /* jobs: 1 the FICH to decode, 2 the VW (4 done); the decoders; the payload buffers written and decoded */
  uint8_t jf, jv, fok, fi, fn, dt, miss, done, wb, db;
  yd_f5_t f5;
  yd_v7_t v7;
  /* the call: counts; the VW frame decoding (its frame number); the message given out (bytes,
     all ones: the summary first), the superframes filled in and its CRC-16. Not given out
     during the call: a line's ~5 ms pause cost superframes on the emulated K6 */
  uint32_t nfr, nfich, nsf, nsfok;
  uint32_t vf, nout, nmiss, crc;
  uint32_t nfin;                       /* VW decodes finished at once (the next payload came) */
#ifdef YX_BENCH
  /* the FICH symbols' levels this call, in soft units (the +1 level 32, the +3 level 96) /16 +
     8: the levels' spread for the bench ("Y h" lines) */
  uint16_t hist[16];
#endif
#ifdef YX_SNAP
  /* the bench's look at the signal (a YX_SNAP build): the filter's 240 points (sync and FICH)
     of the third frame of the first call that has one, its sync's rho^2 (Q8), quarter point,
     +1 level and DC; kept in the message buffer's last 240 bytes */
  int8_t *snap;
  uint32_t snapped, sr, sq, su, sdc, sout;
#endif
#ifdef YX_TRACE
  /* the bench's look at a call's frames (a YX_TRACE build, "Y t" lines after the summary):
     each frame's sync rho^2 (Q8, /2), its offset from where it was due (quarter points), and
     flags (0x80 the FICH good, 0x40 a wide search, 0x20 a VW decoded, its good superframes'
     bits below); the grid's step as the call began and ended */
  uint8_t tr[YX_TR][3];
  uint32_t tw0, tw1, tout;
#endif
  srx_state m;
} yx_t;

/* the message buffer: n bytes at b (6 a superframe and a bit for each); 0 if under min
   superframes */
static unsigned yx_mem(yx_t *x, uint8_t *b, uint32_t n, unsigned min) {
  uint32_t r, max;
  max = n ? yd_div((n - 1u) * 8u, 49u, &r) : 0;
  if (max < min) return 0;
#ifdef YX_SNAP
  /* at the message's far end: only a message that fills the buffer writes over it */
  if (max * SRX_SF < 240u) return 0;
  x->snap = (int8_t *)b + max * SRX_SF - 240u;
#endif
  srx_init(&x->m, b, b + max * SRX_SF, max);
  return max;
}

/* Catmull-Rom between the filter's points (as dmrdec.inc's dx_mf): f at P quarter points */
static const int16_t YX_CR[4][4] = {{0, 128, 0, 0}, {-9, 111, 29, -3}, {-8, 72, 72, -8}, {-3, 29, 111, -9}};

static int32_t yx_mf(const ds_t *d, uint32_t P) {
  uint32_t g = P >> 2;
  const int16_t *c = YX_CR[P & 3];
  int32_t a;
  if (!(P & 3u)) return d->f[g & (DS_FRING - 1)];  /* on a point (the search) */
  a = c[0] * d->f[(g - 1u) & (DS_FRING - 1)] + c[1] * d->f[g & (DS_FRING - 1)] +
              c[2] * d->f[(g + 1u) & (DS_FRING - 1)] + c[3] * d->f[(g + 2u) & (DS_FRING - 1)];
  return a >> 7;
}

/* the sync's correlation with its last symbol at P: c (weights 5 x level - 3), and the sums of
   the 20 values and their squares */
static int32_t yx_corr(const ds_t *d, uint32_t P, int32_t *s1, uint32_t *s2) {
  int32_t c = 0, a = 0;
  uint32_t q = 0, i;
  for (i = 0; i < YD_SYNC_N; i++) {
    int32_t v = yx_mf(d, P - 8u * (YD_SYNC_N - 1u - i));
    c += (5 * YD_SYNC[i] - 3) * v;
    a += v;
    q += (uint32_t)(v * v);
  }
  *s1 = a, *s2 = q;
  return c;
}

/* rho^2 in Q8: 20 c^2 / (2920 (20 s2 - s1^2)) = c^2 128 / (73 E) */
static uint32_t yx_r2(int32_t c, int32_t s1, uint32_t s2) {
  uint32_t ac = (uint32_t)(c < 0 ? -c : c), as = (uint32_t)(s1 < 0 ? -s1 : s1);
  uint32_t E = 20u * s2 - as * as, den = (73u * E) >> 7;
  if (!den || 20u * s2 < as * as) return 0;
  return ds_udiv(ac * ac, den);
}

/* the frame's scale and DC from its sync at P (c, s1 as yx_corr gave them) */
static void yx_scale(yx_t *x, uint32_t P, int32_t c, int32_t s1) {
  int32_t u, m;
  x->inv = c < 0;
  x->P0 = P;
  /* the +1 level: |c| / 584 (c = u sum w level, the weights summing to 0) */
  u = (int32_t)ds_udiv((uint32_t)(c < 0 ? -c : c), YD_WL);
  if (u < 2) u = 2;
  x->u = u;
  x->k16 = (int32_t)ds_udiv((uint32_t)YX_K << 16, (uint32_t)u);
  /* the DC: the mean less the unit x the sync's mean level (12 / 20) */
  m = s1 - (x->inv ? -12 : 12) * u;
  x->dc = m < 0 ? -(int32_t)ds_udiv((uint32_t)-m, 20u) : (int32_t)ds_udiv((uint32_t)m, 20u);
}

/* a soft bit in 4 bits: 8 soft units a step, -7..7 */
static uint32_t yx_q4(int32_t v) {
  v = (v + 4) >> 3;
  return (uint32_t)(v > 7 ? 7 : v < -7 ? -7 : v) & 15u;
}

/* the call is over: what it got waits for housekeeping */
static void yx_end(yx_t *x) {
  x->on = 0, x->cand = 0;
#ifdef YX_TRACE
  x->tout = 0;                          /* (the trace's lines: the first one next) */
#endif
  if (x->nfr) x->done = 1, x->nout = 0xFFFFFFFFu;   /* (the summary first) */
}

static void yx_work(yx_t *x);

/* the work yx_work does next, for the time lines: 2 the FICH, 3 a VW's start, 4 a K=7 step, 5
   a traceback, 6 the VW's end, 7 none (0 and 1 are yx_poll's: searching, in a frame) */
static inline uint32_t yx_kind(const yx_t *x) {
  if (x->jf) return 2;
  if (x->jv == 2) return x->v7.tb ? 5u : 4u;
  return x->jv == 1 ? 3u : x->jv == 4 ? 6u : 7u;
}

#ifdef YX_TRACE
/* into frame f's trace (frames past YX_TR not kept): its sync's rho^2 (Q8) /2, its offset,
   flags */
__attribute__((noinline)) static void yx_tr(yx_t *x, uint32_t f, uint32_t r, int32_t e, uint32_t fl) {
  if (f < YX_TR) x->tr[f][0] |= (uint8_t)(r >> 1), x->tr[f][1] |= (uint8_t)e, x->tr[f][2] |= (uint8_t)fl;
}
#endif

/* a decoded VW frame's good superframes into their places: the message's 0 comes from the first
   frame with a good FICH (a VW frame's, even with no good superframe) or, before one, with a good
   superframe (a frame whose FICH failed is decoded as VW: the header, with its FICH missed, gave
   garbage and shifted the message by a frame where the grid was being timed) */
static void yx_vw_done(yx_t *x) {
  uint8_t sf[30];
  uint32_t ok = yd_v7_end(&x->v7, sf), k, f0;
  x->jv = 0;
  x->nsf += YD_NSF;
  if (!x->m.based && (ok || x->fok)) {
    /* a good FICH's frame number places the message's first frame (FN 0) even when the frames
       before were lost: frames that early are the grid's timing (YX_WIDE) */
    f0 = x->fok && x->fn <= x->vf ? x->vf - x->fn : x->vf;
    x->m.based = 1, x->m.base = (int32_t)(f0 * YD_NSF);
  }
#ifdef YX_TRACE
  yx_tr(x, x->vf, 0, 0, 0x20u | ok);
#endif
  for (k = 0; k < YD_NSF; k++)
    if ((ok >> k) & 1u) x->nsfok++, srx_put(&x->m, (int32_t)(x->vf * YD_NSF + k), sf + YD_SF * k);
    else x->m.bad++;
}

/* the sync's correlation at P, kept if the best so far (of the polarity want, -1 either) */
static void yx_try(yx_t *x, const ds_t *d, uint32_t P, int32_t want) {
  int32_t s1, c;
  uint32_t s2, r;
  c = yx_corr(d, P, &s1, &s2);
  if (want >= 0 && (c < 0) != (want != 0)) return;
  r = yx_r2(c, s1, s2);
  if (r > x->rr) x->rr = r, x->rP = P, x->rc = c, x->rs = s1;
}

/* new grid points: the search, or the frame's next symbols (a few a call). A correlation is
   ~20 interpolations, so one a call where several are tried: emulated, the seven tried at once
   where the next sync was due outlasted the DMA's ring */
/* the grid's step from a sync e quarter points off where it was due a frame on: the whole way
   (sh 0) or 1/8 (sh 3) */
static void yx_time(ds_t *d, int32_t e, uint32_t sh) {
  uint32_t dw = ds_udiv((d->w16 >> sh) * (uint32_t)(e < 0 ? -e : e), 8u * YD_FRAME_SYMS);
  d->w16 += e < 0 ? (uint32_t)-dw : dw;
}

static void yx_poll(yx_t *x, ds_t *d) {
  uint32_t A = d->j - 9u, k;          /* f[] is in up to d->j - 7; the interpolation reaches +2 */
  if (!x->on) {
    if (x->cand == 2) {               /* the candidate's best quarter point, one a call */
      yx_try(x, d, (x->cg << 2) + (uint32_t)x->rq, -1);
      if (++x->rq <= 2) return;
      x->cand = 0;
      yx_scale(x, x->rP, x->rc, x->rs);
      x->on = 1, x->n = YD_SYNC_N, x->miss = 0;
      /* a new call: the last one's lines not given out yet are dropped */
      x->done = 0, x->nfr = x->nfich = x->nsf = x->nsfok = 0;
      x->jf = 0, x->jv = 0;                  /* (the last call's frame still decoding: dropped) */
      x->rlock = 0;                          /* (the call times the grid again) */
#ifdef YX_TRACE
      for (k = 0; k < 3u * YX_TR; k++) (&x->tr[0][0])[k] = 0;
      x->tw0 = x->tw1 = d->w16;
      yx_tr(x, 0, x->rr, 0, 0);
#endif
#ifdef YX_BENCH
      for (k = 0; k < 16; k++) x->hist[k] = 0;
#endif
      srx_init(&x->m, x->m.frames, x->m.have, x->m.max);
      return;
    }
    if ((int32_t)(A - x->g) > (int32_t)(DS_FRING / 2) || (int32_t)(x->g - A) > (int32_t)DS_FRING)
      x->g = A - 48u;                 /* after a long pause, or at the start */
    for (k = 0; k < 2 && (int32_t)(A - x->g) >= 0; k++, x->g++) {
      int32_t s1, c;
      uint32_t s2, r2;
      c = yx_corr(d, x->g << 2, &s1, &s2);
      r2 = yx_r2(c, s1, s2);
      if (r2 >= YX_THR_Q8 && (!x->cand || r2 > x->cr2)) x->cand = 1, x->cg = x->g, x->cr2 = r2;
      if (x->cand && x->g - x->cg > 2u) {
        x->cand = 2, x->rq = -2, x->rr = 0, x->rP = x->cg << 2, x->rc = x->rs = 0;
        return;
      }
    }
    return;
  }
  for (k = 0; k < 4; k++) {
    uint32_t P = x->P0 + 8u * (x->n == YD_FRAME_SYMS ? YD_FRAME_SYMS : x->n - (YD_SYNC_N - 1u));
    int32_t v, l0, l1, av, e;
    if (x->n == YD_FRAME_SYMS) {
      /* the next sync, due 480 symbols on (P: its last symbol): the best of the quarter points
         around it (one a call) of this call's polarity, taken even when weak (the flywheel) */
      if (!x->rf) {                             /* the grid not timed yet: every grid point */
        if ((int32_t)(A - ((P + YX_WIDE + 3u) >> 2)) < 0) return;
        yx_try(x, d, P + (uint32_t)x->rq, x->inv);
        if ((x->rq += 4) <= YX_WIDE) return;
        x->rf = 1, x->rq = (int32_t)(x->rP - P) - 3, x->rend = x->rq + 6;
        return;
      }
      if ((int32_t)(A - ((P + (uint32_t)x->rend + 3u) >> 2)) < 0) return;
      yx_try(x, d, P + (uint32_t)x->rq, x->inv);
      if (++x->rq <= x->rend) return;
#ifdef YX_TRACE
      yx_tr(x, x->nfr, x->rr, (int32_t)(x->rP - P), x->rlock ? 0 : 0x40u);  /* (+-99 at most) */
#endif
      e = (int32_t)(x->rP - P);
      /* a weak best is a miss when the search was wide (73 tries of noise: in bench run 14 such
         bests held the receiver for calls of up to 311 frames of noise) or, timed, at the
         window's edge (the sync further out: looked for wide next) */
      if (x->rr < YX_THR_Q8 && (!x->rlock || e >= YX_LOCKW || e <= -YX_LOCKW)) x->rr = 0;
      if (x->rr >= YX_FLY_Q8) {
        /* the grid's step from the offset a frame (two after a miss: bench run 13, where each
           sync after a miss set the step twice over and the next one was as far off the other
           way): 1/8 of the way, 1/2 when 3 or more off, the whole way when not timed and strong */
        if (x->miss) e >>= 1;
        if (x->rlock) yx_time(d, e, e >= 3 || e <= -3 ? 1u : 3u);
        else yx_time(d, e, 0), x->rlock = x->timed = 1;
        yx_scale(x, x->rP, x->rc, x->rs);
        x->miss = 0;
#ifdef YX_TRACE
        x->tw1 = d->w16;
#endif
      } else {
        x->P0 = P;
        x->rlock = 0;                           /* (the next sync looked for wide) */
        if (++x->miss >= 2) {
          yx_end(x);
          return;
        }
      }
      x->n = YD_SYNC_N;
      continue;
    }
    if ((int32_t)(A - ((P + 3u) >> 2)) < 0) return;
    v = yx_mf(d, P) - x->dc;
    if (x->inv) v = -v;
    av = v < 0 ? -v : v;
    l0 = (v * x->k16) >> 16;                    /* the sign bit: 1 below 0 */
    l1 = ((2 * x->u - av) * x->k16) >> 16;      /* the size bit: 1 outside +-2 */
    if (x->n < YD_SYNC_N + YD_FICH_SYMS) {
#ifdef YX_BENCH
      av = ((v * x->k16) >> 16) + 128;
      x->hist[av < 0 ? 0 : av > 255 ? 15 : av >> 4]++;
#endif
      x->fich[yd_fich_dibit(x->n - YD_SYNC_N)] = (uint8_t)(yx_q4(l0) | (yx_q4(l1) << 4));
      if (++x->n == YD_SYNC_N + YD_FICH_SYMS) {
        x->jf = 1, yd_f5_start(&x->f5);
#ifdef YX_SNAP
        if (!x->snapped && x->nfr == 2) {      /* (the third frame: nfr counts on at its end) */
          uint32_t i, g0 = ((P + 3u) >> 2) - 239u;
          for (i = 0; i < 240u; i++) x->snap[i] = d->f[(g0 + i) & (DS_FRING - 1)];
          x->snapped = 1, x->sq = (x->P0 - (g0 << 2)), x->su = (uint32_t)x->u, x->sdc = (uint32_t)x->dc;
          x->sr = x->rr, x->sout = 0;
        }
#endif
      }
    } else {
      uint32_t b = 2u * (x->n - (YD_SYNC_N + YD_FICH_SYMS)), q;
      if (b == 0) x->pn = 0x1FF, x->wb ^= 1u;   /* the other buffer (the one decoded before last) */
      q = yx_q4(yd_pn9(&x->pn) ? -l0 : l0);
      x->pay[x->wb][b >> 1] = (uint8_t)(q | (yx_q4(yd_pn9(&x->pn) ? -l1 : l1) << 4));
      if (++x->n == YD_FRAME_SYMS) {
        if (x->jv) x->nfin++;
        while (x->jv) yx_work(x);               /* the last frame's VW still decoding: finish it */
        x->jv = 1, x->db = x->wb, x->vf = x->nfr++;   /* this one's to decode (after the FICH) */
        x->rr = 0;                             /* the next sync's tries */
        if (x->rlock) x->rf = 1, x->rq = -YX_LOCKW, x->rend = YX_LOCKW;
        else x->rf = 0, x->rq = -YX_WIDE;
      }
    }
  }
}

/* a step of the decoders, in a gap between samples: the FICH first, then the VW's K=7 steps,
   its traceback 16 steps a call, the CRCs */
static void yx_work(yx_t *x) {
  if (x->jf) {
    if (yd_f5_step(&x->f5, x->fich)) {
      uint8_t b[4];
      x->jf = 0;
      x->fok = (uint8_t)yd_f5_end(&x->f5, b);
      if (x->fok) {
        x->nfich++;
#ifdef YX_TRACE
        yx_tr(x, x->nfr, 0, 0, 0x80u);
#endif
        x->fi = b[0] >> 6, x->fn = (b[1] >> 3) & 7u, x->dt = b[2] & 3u;
      }
    }
    return;
  }
  if (x->jv == 1) {
    /* a header, terminator or other data type has no VW to decode */
    if (x->fok && (x->fi != 1 || x->dt != 3)) {
      x->jv = 0;
      if (x->fi == 2) yx_end(x);
      return;
    }
    yd_v7_start(&x->v7);
    x->jv = 2;
  }
  if (x->jv == 2) {
    if (!x->v7.tb) yd_v7_step(&x->v7, x->pay[x->db]);
    else if (yd_v7_tb(&x->v7, 16u) && x->v7.step == YD_V7_STEPS) x->jv = 4;
  } else if (x->jv == 4) {
    yx_vw_done(x);
  }
}

static const char YX_H[] = "0123456789abcdef";

/* a line of up to 24 bytes of b[0..n) from *at - o (on: *at), its place first */
static char *yx_hex(char *line, const char *tag, uint32_t *at, uint32_t o, const uint8_t *b, uint32_t n) {
  uint32_t i = *at - o, k;
  char *p = kv(line, tag, (int)i);
  *p++ = ' ';
  for (k = 0; k < 24u && i < n; k++, i++) *p++ = YX_H[b[i] >> 4], *p++ = YX_H[b[i] & 15u];
  *at = i + o;
  return p;
}

/* housekeeping's line once the call is over (0 when none): its summary, then the superframes,
   24 bytes a line */
static char *yx_house(yx_t *x, char *line) {
  char *p;
  uint32_t i, n;
  if (!x->done || x->jv || x->jf) return 0;
  n = x->m.n * SRX_SF;
  if (x->nout == 0xFFFFFFFFu) {
    x->nout = 0;
    x->nmiss = srx_conceal(&x->m);
    x->crc = yd_crc16(x->m.frames, (int)n);
    p = kv(kv(line, "Y call fr=", (int)x->nfr), " fich=", (int)x->nfich);
    p = kv(kv(p, " vw=", (int)x->nsfok), "/", (int)x->nsf);
    p = kv(kv(p, " sf=", (int)x->m.n), " miss=", (int)x->nmiss);
    if (x->m.over) p = kv(p, " over=", (int)x->m.over);
    p = put(p, " crc=");
    for (i = 4; i-- > 0;) *p++ = YX_H[(x->crc >> (4 * i)) & 15u];
#ifdef YX_BENCH
    x->nout = 0xFFFFFFFEu;
#endif
    return p;
  }
#ifdef YX_TRACE
  if (x->tout < 0x100u) {                /* the trace: the grid's step, then 8 frames a line */
    uint32_t nf = 3u * (x->nfr < YX_TR ? x->nfr : YX_TR);
    if (x->tout == 0) {
      x->tout = 1;
      return kv(kv(line, "Y t w=", (int)x->tw0), " ", (int)x->tw1);
    }
    if (x->tout - 1u < nf) return yx_hex(line, "Y t ", &x->tout, 1u, &x->tr[0][0], nf);
    x->tout = 0x100u;
  }
#endif
#ifdef YX_BENCH
  if (x->nout >= 0xFFFFFFFDu) {          /* the FICH levels: -128..-1, then 0..127 */
    i = x->nout == 0xFFFFFFFEu ? 0 : 8;
    p = put(line, i ? "Y h+" : "Y h-");
    for (n = i + 8; i < n; i++) p = kv(p, " ", (int)x->hist[i]);
    x->nout = x->nout == 0xFFFFFFFEu ? 0xFFFFFFFDu : 0;
    return p;
  }
#endif
#ifdef YX_SNAP
  if (x->snapped == 1) {                 /* the snapshot: its sync, then 24 points a line */
    if (x->sout == 0) {
      p = kv(kv(line, "Y s r2=", (int)x->sr), " q=", (int)x->sq);
      p = kv(kv(p, " u=", (int)x->su), " dc=", (int)x->sdc);
      p = kv(p, " inv=", (int)x->inv);
      x->sout = 1;
      return p;
    }
    p = kv(line, "Y s ", (int)(x->sout - 1u));
    *p++ = ' ';
    for (i = 0; i < 24u; i++, x->sout++)
      *p++ = YX_H[(uint8_t)x->snap[x->sout - 1u] >> 4], *p++ = YX_H[(uint8_t)x->snap[x->sout - 1u] & 15u];
    if (x->sout > 240u) x->snapped = 2;
    return p;
  }
#endif
  if (x->nout >= n) {
    x->done = 0;
    return 0;
  }
  return yx_hex(line, "Y d ", &x->nout, 0, x->m.frames, n);
}
