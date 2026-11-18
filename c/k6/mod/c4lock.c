/* c4lock: YSF (C4FM) from the K6's own FD6818 with the MCU locked to the chip's symbol clock, run 3
   of docs/k6-c4fm.md.
   Runs 1 and 2 (c4probe, c4ysf) sent YSF by putting each symbol's sign in the FSK modem's FIFO
   (4800 baud) and rewriting REG_70's tone-2 gain on the symbol edges for its size. The Pluto
   decoded the frames, but the size writes drifted along long packets (the MCU's TIM17 runs about
   520 ppm slow against the chip) and each packet started up to a symbol off (the chip starts on
   its own bit clock). The captures also showed that the chip's 4800-baud grid runs on, unbroken,
   from packet to packet through a key-up. So this module learns that grid and times everything
   on it:
     - the chip's symbol in MCU cycles and its phase, from the TX-done flag (REG_0C bit 0) polled
       after every packet, fitted by least squares over the last NFIT packets (the flag rises on
       the grid; a poll brackets it within one register read);
     - where a packet starts against its enable: C0 sets the grid, then C1..C16 put the enable at
       each eighth of a symbol in turn. The start moves a whole symbol where the enable crosses the
       chip's threshold; the data packets put their enable half a symbol from it, so they all start
       on a known tick;
     - the size writes at that tick's grid plus a lag: C4_LAG4 quarter symbols (run 3's best)
       less the mean enable-to-done time over the eight phases, which is what runs 1 and 2 saw on
       average.
   Since 11-18 the MCU's clock can sit 1500 ppm off the chip's (run 4: 520), too far for the fit
   to pull in from C4_PPM, so first come
     R0, R1    128 words each, timed from the inside by the FIFO almost-empty flag (c4fifo.c): every
               2-word refill brings the flag back 32 chip symbols on; the line through the 44
               marks gives the chip's symbol in MCU cycles, and their mean starts the fit
   Then it sends
     A00..A12  one frame a packet (header, data FN 0, FN 1 "AG7EW K6 C4FM", terminator in turn),
               size writes at -6..+6 quarter symbols from that lag
     B0..B4    the whole transmission (four frames back to back) in one packet, at -2..+2 quarter
               symbols, each just after a rate packet of its own (R2..R6): its marks set the rate
               and phase for the B packet (11-18 lock5: the clock wanders a few hundred ppm
               within seconds, and the B packets slid up to 0.9 symbol on the fit's grid)
   Every data packet ends with 16 outer symbols, so the TX-done poll has room after the last size
   write. Like c4ysf it keys C4_PRE_MS after the bench job's trigger carrier, on 440.850 only, the
   PA off by default, and ends with AG7EW in MCW. A Pluto capture goes to
   python3 -m modem.k6c4fm analyze CAP.wav --plan lock.

   It says
     K6 MOD say c4lock n=42 g=78/27 lag=9 ppm=-630 pa=0 rx=440850000
     K6 MOD say c4 trigger heard
     K6 MOD say c4lock cal ppm=... th=.../16 u=... ub=...     the fitted rate (0.1 ppm, chip
                                    against MCU), the data packets' enable phase, the enable-to-done
                                    time at that phase and on average (% of a symbol)
     K6 MOD say c4lock v=...        each phase's enable-to-done time plus its phase (% of a symbol,
                                    against C1's): one step of 100 where the start jumps a symbol
     K6 MOD say c4lock rate ppm=... ok=1111111 phi=...   R0 and R1's mean (0.1 ppm, as fit=),
                                    the rate packets that ran whole, and the done grid against
                                    R2's centre mark (% of a symbol)
     K6 MOD say c4lock rq ...       each rate packet's rate (0.1 ppm)
     K6 MOD say c4lock w=... late=... miss=... lost=... wr=...-... rd=...   size writes, late, data
                                    packets that ended over half a symbol off their tick, polls that
                                    missed the rise or came in 3/4 symbol off, a write's length and
                                    the longest poll (cycles at 96 MHz)
     K6 MOD say c4lock fit ppm=... res=...    the final rate and the last fit's worst residual
     K6 MOD say c4lock sent n=42 ms=... id=52
   and returns the packets sent, or a negative error as c4ysf's (-8 writes too slow, -9 a packet
   never finished, -10 the calibration missed a phase).

   Build: python -m modem.k6 mod out.wav c/k6/mod/c4lock.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 3
#define K6_MODULE_OPT z     /* -Oz: c4sotto with the rate packets is 12.1 KB at -Os, over the 11,776 B module space */
#include "../k6_mod.h"
#include "k6ui.h"
#ifndef C4_SOTTO
#define C4_SOTTO 0          /* 1 (c4sotto.c, with C4_DEMO): the sotto clip's data calls in the table */
#endif
#include "c4probe_frames.h"   /* C4_PN, C4_YSF_SIGN, C4_YSF_SIZE */
#ifndef K6_NAME
#define K6_NAME "C4FM LOCK"
#endif
K6_MODULE_NAME(K6_NAME);

#ifndef C4_HZ
#define C4_HZ 440850000u
#endif
#ifndef C4_PA
#define C4_PA 0
#endif
#ifndef C4_GAIN
#define C4_GAIN 78          /* REG_70 tone-2 gain for +-2.7 kHz (run 2: 2.7 kHz) */
#endif
#ifndef C4_INNER
#define C4_INNER 27         /* +-0.9 kHz (run 2: 1.0 kHz) */
#endif
#ifndef C4_LAG4
#define C4_LAG4 9           /* the size writes' lag, quarter symbols (run 3: best between 8 and 10) */
#endif
#ifndef C4_PPM
#define C4_PPM (-630)       /* the chip's symbol against the MCU's 20000 cycles, before the fit (run 3) */
#endif
#ifndef C4_DEMO
#define C4_DEMO 0           /* 1 (c4ftm.c): no sweep; DN and data transmissions at the lag, 1 s apart */
#endif
#ifndef C4_CFG
#define C4_CFG 0x00E1
#endif
#ifndef C4_REG72
#define C4_REG72 0xC195
#endif
#ifndef C4_PRE_MS
#define C4_PRE_MS 10000
#endif
#ifndef C4_LEAD_MS
#define C4_LEAD_MS 300
#endif
#ifndef C4_CLEAR_DBM
#define C4_CLEAR_DBM -90
#endif
#ifndef C4_CLEAR_MS
#define C4_CLEAR_MS 1000
#endif
#ifndef C4_WAIT_MS
#define C4_WAIT_MS 180000
#endif
#ifndef ID_TEXT
#define ID_TEXT "AG7EW"
#endif
#ifndef ID_WPM
#define ID_WPM 20
#endif
#ifndef ID_HZ
#define ID_HZ 800
#endif
#ifndef ID_GAIN
#define ID_GAIN 60
#endif

#define RCC_APB2ENR 0x40021018u
#define TIM14_CNT 0x40002024u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define TIM17EN (1u << 18)

#define SYM 20000u                                  /* cycles a symbol: 96 MHz / 4800 */
#define P12_0 ((uint32_t)(81920000 + (C4_PPM) * 8192 / 100))   /* SYM in Q12, the prior rate */
#define LEAD_SYMS 160u                              /* 16 preamble bytes, 4 sync bytes */
#define FRAME_SYMS 480u
#define FRAME_WORDS 30u
#define PAD_WORDS 1u                                /* 16 outer symbols after the frames */
#define FIFO_WORDS 120u                             /* run 2: the FIFO holds 120 words */
#define CAL_WORDS 4u                                /* a calibration packet's payload: 64 symbols */
#ifndef C4_FIFOPROBE
#define C4_FIFOPROBE 0      /* 1 (c4fifo.c): no YSF; NR rate packets only, every mark reported */
#endif
/* the rate packets (c4fifo, 11-18): the FIFO almost-empty flag, chip-timed, marks every 32 symbols */
#define NM 108                                      /* marks a packet: a refill each */
#define SXX 419868                                  /* sum of (2k - 107)^2, k = 0..107 */
#define P_WORDS 128u                                /* REG_5D's longest packet */
#define P_PRE 20u                                   /* words in the FIFO at the enable */
#define P_T 16u                                     /* almost-empty threshold, REG_5E<9:3> */
#define P_CHUNK 1u                                  /* words a refill: a mark every 16 symbols */
#define IRQ_AE 0x4000
#define RATE_MAX 245760                             /* 3000 ppm in Q12 cycles: a rate past it is wrong */
#if C4_FIFOPROBE
#define NR 2                                        /* rate packets, 1.5 s apart */
#define NCAL 1                                      /* (not sent: the cal and fit arrays make room) */
#define NA 0
#define GAP_MS 1500u                                /* carrier between the probe packets */
#define NFIT 2
#else
#define NR 2                                        /* rate packets before the calibration (and one
                                                       before each B packet: 11-18 lock5, the clock
                                                       wanders a few hundred ppm in seconds) */
#define NCAL 17
#endif
#if C4_FIFOPROBE
#elif C4_DEMO
#define NA 13                                       /* kept: the fit needs them (11-18 ftm3: miss 5 without) */
#define GAP_MS 500u                                 /* carrier between the demo's transmissions (and a
                                                       rate packet, 0.5 s) */
#else
#define NA 13
#define GAP_MS 0u
#endif
#define A_FIRST (-6)
#define NB 5
#if C4_FIFOPROBE
#define NPKT NR
#else
#define NPKT (NR + NCAL + NA + 2 * NB)
#define NFIT 16                                     /* packets in the rate and phase fit */
#endif
#define SPAN_FIT 2048                               /* symbols spanned before the rate is fitted */
#define WR_MAX (SYM * 3u / 4u)                      /* a write longer than this can't keep up */
#define POLL_MAX (96u * 40000u)                     /* 40 ms of TX-done polling */
#if C4_FIFOPROBE
#define TX_MS (C4_LEAD_MS + NR * (500u + GAP_MS) + 400u + ID_UNITS * UNIT_MS + 1500u)
#else
#define TX_MS (C4_LEAD_MS + NR * 500u + NCAL * 90u + NA * 180u + NB * (990u + GAP_MS) + 400u \
               + ID_UNITS * UNIT_MS + 1500u)
#endif

#define R_IRQ 0x02
#define R_STATUS 0x0C
#define R_AF 0x2B
#define R_IRQ_EN 0x3F
#define R_DEV 0x40
#define R_CFG 0x58
#define R_CTRL 0x59
#define R_FIFO 0x5F
#define R_TONE 0x70
#define R_TONE1 0x71
#define IRQ_TX_DONE 0x8000
#define CTRL_BASE 0x00F8                            /* 16 preamble bytes, 4-byte sync, no scrambler */
#define CTRL_CLR 0xC000
#define CTRL_TX_EN 0x0800
#define G(g) ((uint16_t)(0x0080u | ((g) & 0x7Fu)))  /* REG_70: tone 2 (the FSK) on at gain g */
#define REG70_ID ((uint16_t)(0x8000u | ((ID_GAIN & 0x7Fu) << 8)))
#define WORD(hz) ((uint16_t)((uint32_t)(hz) * 1032444u / 100000u))
#define UNIT_MS (1200u / ID_WPM)
#define ID_UNITS 52                                 /* AG7EW: A 8, G 12, 7 16, E 4, W 12 */

#if C4_DEMO && C4_SOTTO
static const int8_t BQ[NB] = {0, 0, 0, 0, 0};
static const uint8_t BSET[NB] = {1, 3, 4, 5, 2};    /* DN, the sotto clip's 3 data calls, DN (k6c4fm.SOTTO_SETS) */
static const uint8_t BINV[NB] = {0, 0, 0, 0, 0};
#elif C4_DEMO
static const int8_t BQ[NB] = {0, 0, 0, 0, 0};
static const uint8_t BSET[NB] = {1, 2, 1, 2, 0};    /* frame set: 0 data, 1-2 DN (k6c4fm.DEMO_SETS) */
static const uint8_t BINV[NB] = {0, 0, 0, 0, 0};    /* 1: every sign flipped (k6c4fm.DEMO_INV) */
#else
static const int8_t BQ[NB] = {-2, -1, 0, 1, 2};
static const uint8_t BSET[NB] = {0, 0, 0, 0, 0};
static const uint8_t BINV[NB] = {0, 0, 0, 0, 0};
#endif

/* the registers the module changes, put back on the way out */
static const uint8_t SAVED[] = {R_CFG, R_CTRL, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, R_AF, R_IRQ_EN, R_DEV};
#define NSAVED (sizeof SAVED)

static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }

typedef struct {
  uint32_t t, c17, c14;     /* 96 MHz cycles, unwrapped from TIM17 and TIM14 */
  uint32_t apb2;
} clk_t;

typedef struct {
  const k6_api_t *api;
  clk_t c;
  uint32_t w, late, wmin, wmax, len, miss, lost, odd, rmax;
  /* the chip's grid in MCU cycles: tick(n) = ta + (n - na) x p12 / 4096 */
  uint32_t p12, ta;
  int32_t na;
  uint32_t fe[NFIT];        /* the fit's TX-done times and their ticks (a ring) */
  int32_t fn[NFIT];
  uint32_t nfit, put;
  int32_t res;              /* the last fit's worst residual, cycles */
  uint32_t cx[NCAL];        /* the calibration packets' enables and done ticks */
  int32_t cn[NCAL];
  uint8_t cok[NCAL];
  uint32_t th;              /* the data packets' enable phase, sixteenths of a symbol */
  int32_t ustar, ubar;      /* enable to done less the packet: at th, and on average (cycles) */
  int32_t v[8];
  int32_t cppm;             /* the rate after the calibration, 0.1 ppm */
  uint32_t settled;
  uint16_t sv[NSAVED];
  uint16_t r40;
  int32_t rq[NR + NB];      /* each rate packet's symbol less P12_0 (Q12 cycles) */
  uint32_t rok;             /* the rate packets that ran whole (bits) */
  int32_t rppm;             /* the first ones' rate, 0.1 ppm (or C4_PPM's) */
  uint32_t tc;              /* the last rate packet's centre mark (its line's, at mark 53.5) */
  int32_t phi;              /* a done tick less the centre mark's time, mod a symbol (cycles) */
  uint32_t rsync;           /* 1: the grid from the rate packets (phi set), not the fit */
#if C4_FIFOPROBE
  int16_t pm[NR][NM];       /* mark k less the first and k nominal periods (C4_PPM), 16 cycles */
  uint32_t px[NR], p0[NR], pe[NR];   /* each packet's enable, first mark and TX done (cycles) */
  uint16_t pb[NR];          /* the REG_02 bits seen, ORed */
  uint8_t pn[NR], pbad[NR]; /* marks; flags with neither bit, or a mark past NM */
  int8_t pr[NR];            /* 0, or -9: a flag never came (the packet stopped) */
#endif
} c4_t;

static uint32_t now(clk_t *c) {
  uint32_t c17 = rd(TIM17_CNT) & 0xFFFF, c14 = rd(TIM14_CNT) & 0xFFFF;
  uint32_t coarse = ((c14 - c->c14) & 0xFFFF) * 9600u, fine = (c17 - c->c17) & 0xFFFF;
  int32_t d = (int32_t)(int16_t)(uint16_t)(fine - coarse);
  c->c17 = c17, c->c14 = c14;
  c->t += coarse + (uint32_t)d;
  return c->t;
}

static void clk_on(clk_t *c) {
  c->apb2 = rd(RCC_APB2ENR);
  wr(RCC_APB2ENR, c->apb2 | TIM17EN);
  wr(TIM17 + 0x00, 0);                  /* CR1: stopped */
  wr(TIM17 + 0x28, 0);                  /* PSC: 96 MHz */
  wr(TIM17 + 0x2C, 0xFFFF);             /* ARR */
  wr(TIM17 + 0x14, 1);                  /* EGR: load them */
  wr(TIM17 + 0x00, 1);                  /* CR1: count */
  c->t = 0;
  c->c17 = rd(TIM17_CNT) & 0xFFFF, c->c14 = rd(TIM14_CNT) & 0xFFFF;
}

static void clk_off(clk_t *c) {
  wr(TIM17 + 0x00, 0);
  wr(RCC_APB2ENR, c->apb2);
}

/* busy-wait until cycle t: api->delay_us for all but the last 30 us, then the counter */
static void wait_to(const k6_api_t *api, clk_t *c, uint32_t t) {
  int32_t rem;
  while ((rem = (int32_t)(t - now(c))) > 96 * 60) {
    uint32_t us = (((uint32_t)rem * 683u) >> 16) - 30u;  /* rem / 96 (no divide on the M0) */
    api->delay_us(us > 200u ? 200u : us);
  }
  while ((int32_t)(t - now(c)) > 0) {}
}

/* n / d by shift and subtract (no divide on the M0); d < 2^31 */
static uint32_t udiv(uint32_t n, uint32_t d, uint32_t *rem) {
  uint32_t q = 0, r = 0;
  int i;
  for (i = 31; i >= 0; i--) {
    r = (r << 1) | ((n >> i) & 1u);
    if (r >= d) { r -= d; q |= 1u << i; }
  }
  if (rem) *rem = r;
  return q;
}

/* n / d (d > 0) with f fraction bits, signed, rounded toward zero */
static int32_t sdivq(int32_t n, uint32_t d, int f) {
  uint32_t r, q = udiv((uint32_t)(n < 0 ? -n : n), d, &r);
  while (f--) {
    r <<= 1;
    q <<= 1;
    if (r >= d) { r -= d; q |= 1u; }
  }
  return n < 0 ? -(int32_t)q : (int32_t)q;
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *dec(char *p, uint32_t v) {     /* no divide on the M0: subtract powers of ten */
  static const uint32_t pw[] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u,
                                10000u, 1000u, 100u, 10u, 1u};
  int i, started = 0;
  for (i = 0; i < 10; i++) {
    char d = '0';
    while (v >= pw[i]) { v -= pw[i]; d++; }
    if (d != '0' || started || i == 9) { *p++ = d; started = 1; }
  }
  return p;
}

static char *sdec(char *p, int32_t v) {
  if (v < 0) { *p++ = '-'; v = -v; }
  return dec(p, (uint32_t)v);
}

/* ---------------------------------------------------------------- the chip's grid */
/* m chip symbols in MCU cycles (m < 2^20) */
static uint32_t mulp(const c4_t *s, uint32_t m) {
  return m * (s->p12 >> 12) + ((m * (s->p12 & 4095u)) >> 12);
}

static uint32_t tick(const c4_t *s, int32_t n) {
  int32_t d = n - s->na;
  return d >= 0 ? s->ta + mulp(s, (uint32_t)d) : s->ta - mulp(s, (uint32_t)-d);
}

/* the tick nearest t */
static int32_t idx(const c4_t *s, uint32_t t) {
  int32_t d = (int32_t)(t - s->ta), n, h = (int32_t)(s->p12 >> 13);
  uint32_t pi = s->p12 >> 12, m = udiv((uint32_t)(d < 0 ? -d : d) + pi / 2u, pi, 0);
  n = s->na + (d < 0 ? -(int32_t)m : (int32_t)m);
  for (;;) {
    int32_t r = (int32_t)(t - tick(s, n));
    if (r > h) n++;
    else if (r < -h) n--;
    else return n;
  }
}

/* q sixteenths of a symbol in cycles, signed (|q| < 800) */
static int32_t sixteenths(const c4_t *s, int32_t q) {
  int32_t m = (int32_t)(((uint32_t)(q < 0 ? -q : q) * (s->p12 >> 4)) >> 12);
  return q < 0 ? -m : m;
}

/* the first time at or after t on phase th (sixteenths) of the grid */
static uint32_t at_phase(const c4_t *s, uint32_t t, uint32_t th) {
  uint32_t off = (uint32_t)sixteenths(s, (int32_t)th), x;
  int32_t n = idx(s, t - off);
  while ((int32_t)((x = tick(s, n) + off) - t) < 0) n++;
  return x;
}

/* a TX-done time into the fit, on tick n (want: a data packet's, placed on the grid) or else the
   nearest so far; then the line through the last NFIT (phase only until they span SPAN_FIT
   symbols). A time more than 3/8 symbol off its tick stays out (a poll held up). -> its tick */
static int32_t fit_add(c4_t *s, uint32_t e, int32_t want, int have) {
  int32_t n, nl, dm, rm, sdr = 0, sdd = 0, db = 0, lo, worst = 0, sd = 0, sr = 0;
  uint32_t k, cnt;
  if (!s->nfit) {
    s->ta = e;
    s->na = 0;
    n = 0;
  } else {
    int32_t r, q = (int32_t)(s->p12 >> 14);              /* a quarter symbol */
    n = have ? want : idx(s, e);
    r = (int32_t)(e - tick(s, n));
    if (r > 3 * q / 2 || r < -3 * q / 2) {                 /* 3/8 symbol: a poll held up */
      s->odd++;
      return n;
    }
  }
  s->fe[s->put] = e;
  s->fn[s->put] = n;
  s->put = (s->put + 1) % NFIT;
  if (s->nfit < NFIT) s->nfit++;
  cnt = s->nfit;
  nl = n, lo = n;
  for (k = 0; k < cnt; k++) {
    int32_t r = (int32_t)(s->fe[k] - tick(s, s->fn[k]));
    sd += (s->fn[k] - nl) >> 4;
    sr += r;
    if (s->fn[k] < lo) lo = s->fn[k];
  }
  dm = sdivq(sd, cnt, 0);
  rm = sdivq(sr, cnt, 0);
  if (nl - lo >= SPAN_FIT) {
    for (k = 0; k < cnt; k++) {
      int32_t d = ((s->fn[k] - nl) >> 4) - dm, r = (int32_t)(s->fe[k] - tick(s, s->fn[k])) - rm;
      sdr += d * r;
      sdd += d * d;
    }
    if (sdd > 0) db = sdivq(sdr, (uint32_t)sdd, 8);   /* cycles a 16 symbols, Q8 = a symbol, Q12 */
  }
  s->ta = tick(s, nl) + (uint32_t)(rm - ((db * dm) >> 8));
  s->na = nl;
  s->p12 = (uint32_t)((int32_t)s->p12 + db);
  for (k = 0; k < cnt; k++) {
    int32_t r = (int32_t)(s->fe[k] - tick(s, s->fn[k]));
    if (r < 0) r = -r;
    if (r > worst) worst = r;
  }
  s->res = worst;
  return n;
}

/* chip against MCU, 0.1 ppm */
static int32_t ppm10(const c4_t *s) {
  return sdivq(((int32_t)s->p12 - 81920000) * 125, 1024, 0);    /* x 10^7 / 81920000 */
}

/* cycles as % of a symbol */
static int32_t pct(const c4_t *s, int32_t c) {
  return sdivq(c * 100, s->p12 >> 12, 0);
}

/* ---------------------------------------------------------------- the radio */
/* Morse by code, as cwkey.c: start at 1, then 0 for a dot and 1 for a dash */
static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";

static int code_of(char c) {
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c && c != '~') return i;
  return 0;
}

/* wait until ms() reaches t; 0, or -4 on a key */
static int until(const k6_api_t *api, uint32_t t) {
  if (api->key_down()) return -4;
  while ((int32_t)(api->ms() - t) < 0) api->delay_us(250);
  return 0;
}

/* still keyed? tx_pa(C4_PA) keeps the PA as it is; -5 once the core keyed down */
static int keyed(const k6_api_t *api) {
  return api->tx_pa(C4_PA) < 0 ? -5 : 0;
}

/* AG7EW in MCW: tone 1 keyed with REG_70 (the FSK modem off); the units sent, or an error */
static int mcw(const k6_api_t *api) {
  const char *s;
  int units = 0, r = 0;
  uint32_t t = api->ms();
  api->fd_write(R_TONE, 0);
  api->fd_write(R_TONE1, WORD(ID_HZ));
  for (s = ID_TEXT; *s && !r; s++) {
    int c = code_of(*s), b;
    if (*s == ' ') { t += 4 * UNIT_MS; units += 4; continue; }
    for (b = 31; !(c >> b); b--) {}
    while (b-- && !r) {
      int n = (c >> b) & 1 ? 3 : 1, g = b ? 1 : 3;
      if ((r = keyed(api)) != 0) break;
      api->fd_write(R_TONE, REG70_ID);
      t += (uint32_t)n * UNIT_MS;
      if ((r = until(api, t)) != 0) break;
      api->fd_write(R_TONE, 0);
      t += (uint32_t)g * UNIT_MS;
      r = until(api, t);
      units += n + g;
    }
  }
  api->fd_write(R_TONE, 0);
  return r ? r : units;
}

/* one timed register write, started its own length early so it ends (and lands) at t */
static void timed(c4_t *s, uint32_t t, uint8_t reg, uint16_t v) {
  uint32_t a = t - s->len, t0, t1;
  if ((int32_t)(a - now(&s->c)) < 0) s->late++;
  wait_to(s->api, &s->c, a);
  t0 = now(&s->c);
  s->api->fd_write(reg, v);
  t1 = now(&s->c);
  s->len = t1 - t0;
  if (s->len < s->wmin) s->wmin = s->len;
  if (s->len > s->wmax) s->wmax = s->len;
}

static unsigned bit(const uint8_t *b, uint32_t k) {
  return (b[k >> 3] >> (7u - (k & 7u))) & 1u;
}

/* FIFO word i of a packet: frame f0's signs on (nwf words), then outer alternation; nwf 0: C4_PN */
static uint16_t fword(uint32_t f0, uint32_t nwf, uint32_t i) {
  const uint8_t *b = nwf ? C4_YSF_SIGN + f0 * C4_PKT_BYTES : C4_PN;
  if (nwf && i >= nwf) return 0x5555;
  return (uint16_t)(b[2 * i] | b[2 * i + 1] << 8);
}

/* a packet of nw words set up, pre of them in the FIFO, the 20 ms settle: 0 or an error */
static int arm(c4_t *s, uint32_t nw, uint32_t pre, uint32_t f0, uint32_t nwf, uint16_t inv) {
  const k6_api_t *api = s->api;
  uint32_t i;
  int r;
  api->fd_write(R_IRQ_EN, IRQ_TX_DONE);
  api->fd_write(R_CTRL, CTRL_BASE | CTRL_CLR);
  api->fd_write(R_CTRL, CTRL_BASE);
  api->fd_write(0x5D, (uint16_t)((2u * nw - 1u) << 8));
  for (i = 0; i < pre; i++) api->fd_write(R_FIFO, fword(f0, nwf, i) ^ inv);
  if ((r = until(api, api->ms() + 20)) != 0) return r;        /* as the stock MDC path */
  return keyed(api);
}

/* TX done polled from cycle from: 1 and its time in *e (between the last read that saw it low and
   the first that saw it high), 0 if it was up at the first read, -9 never; the packet ended */
static int done(c4_t *s, uint32_t from, uint32_t *e) {
  const k6_api_t *api = s->api;
  uint32_t prev, t, t0;
  int r = -9;
  wait_to(api, &s->c, from);
  t0 = prev = now(&s->c);
  if (api->fd_read(R_STATUS) & 1) {
    r = 0;
    s->lost++;
  } else {
    for (;;) {
      t = now(&s->c);
      if (t - prev > s->rmax) s->rmax = t - prev;
      if (api->fd_read(R_STATUS) & 1) {
        *e = prev + ((t - prev) >> 1);
        r = 1;
        break;
      }
      prev = t;
      if (t - t0 > POLL_MAX) break;
    }
  }
  api->fd_write(R_IRQ, 0);
  api->fd_write(R_CTRL, CTRL_BASE);
  return r;
}

/* calibration packet k: C4_PN's first 64 signs at the outer gain, the enable at phase (k - 1) & 7
   (eighths) once the grid is known; 0 or an error */
static int cal(c4_t *s, uint32_t k) {
  uint32_t x, e = 0, nsym = LEAD_SYMS + CAL_WORDS * 16u, from;
  int r, phased = s->nfit != 0;
  if ((r = arm(s, CAL_WORDS, CAL_WORDS, 0, 0, 0)) != 0) return r;
  x = now(&s->c) + s->len + 96u * 50u;
  if (phased) x = at_phase(s, x, ((k - 1u) & 7u) * 2u);
  timed(s, x, R_CTRL, CTRL_BASE | CTRL_TX_EN);
  from = phased ? x + mulp(s, nsym) + (uint32_t)s->ustar - 2u * SYM : x + (nsym - 6u) * SYM;
  if ((r = done(s, from, &e)) < 0) return r;
  s->cx[k] = x;
  s->cok[k] = (uint8_t)(r ? 1 + phased : 0);
  if (r) {
    if (!s->nfit) s->ustar = (int32_t)(e - x - mulp(s, nsym));
    s->cn[k] = fit_add(s, e, 0, 0);
  }
  return 0;
}

/* from the calibration: each enable's phase on the final fit, its enable to done (u) plus that
   phase (v: flat but for one whole-symbol step where the start jumps a tick), by eighths; the data
   packets' phase half a symbol from the step, their u there, and the mean u */
static int settle(c4_t *s) {
  int32_t v8[8], cnt[8], i, k, bi = 0, best = -0x7FFFFFFF, sum = 0, n = 0, j, th, va, vb;
  int32_t pc = (int32_t)(s->p12 >> 12);
  uint32_t nsym = LEAD_SYMS + CAL_WORDS * 16u;
  for (i = 0; i < 8; i++) v8[i] = cnt[i] = 0;
  for (k = 1; k < NCAL; k++) {
    int32_t u, d;
    if (s->cok[k] != 2) continue;
    u = (int32_t)(tick(s, s->cn[k]) - s->cx[k] - mulp(s, nsym));
    d = (int32_t)(s->cx[k] - tick(s, idx(s, s->cx[k])));  /* -P/2..P/2 */
    if (d < -pc / 16) d += pc;                            /* the phase: -P/16..15P/16 */
    i = sdivq(d * 8 + pc / 2, (uint32_t)pc, 0) & 7;
    v8[i] += u + d;
    cnt[i]++;
    sum += u;
    n++;
  }
  if (n < 8) return -10;
  for (i = 0; i < 8; i++)
    if (cnt[i]) v8[i] = sdivq(v8[i], (uint32_t)cnt[i], 0);
  /* a bin the enables happened to miss takes the one before (the step is one whole symbol: a
     filled bin on either side of it is enough) */
  for (k = 0; k < 16; k++) {
    i = k & 7;
    if (!cnt[i] && cnt[(i + 7) & 7]) {
      v8[i] = v8[(i + 7) & 7] - (i == 0 ? pc : 0);
      cnt[i] = 1;
    }
  }
  for (i = 0; i < 8; i++) {
    int32_t dv = (i < 7 ? v8[i + 1] : v8[0] + pc) - v8[i];
    if (dv > best) best = dv, bi = i;
  }
  for (i = 0; i < 8; i++) s->v[i] = v8[i];
  th = 2 * bi + 9;
  j = bi + 4;
  va = j < 8 ? v8[j] : v8[j - 8] + pc;
  vb = j + 1 < 8 ? v8[j + 1] : v8[j + 1 - 8] + pc;
  s->ustar = sdivq(va + vb, 2, 0) - sixteenths(s, th);
  s->th = (uint32_t)th & 15u;
  s->ubar = sdivq(sum, (uint32_t)n, 0);
  return 0;
}

/* nf frames from f0 in one packet, the size writes q quarter symbols from the lag: 0 or an error */
static int data(c4_t *s, uint32_t f0, uint32_t nf, int32_t q, uint16_t inv) {
  const k6_api_t *api = s->api;
  const uint8_t *sz = C4_YSF_SIZE + f0 * C4_PKT_BYTES;
  uint32_t nwf = nf * FRAME_WORDS, nw = nwf + PAD_WORDS, pre = nw < FIFO_WORDS ? nw : FIFO_WORDS;
  uint32_t pay = nw * 16u, nfs = nf * FRAME_SYMS, k, x, sent, e = 0;
  int32_t ne, off, n0;
  unsigned cur = 1;
  int r;
  if ((r = arm(s, nw, pre, f0, nwf, inv)) != 0) return r;
  x = at_phase(s, now(&s->c) + s->len + 96u * 50u, s->th);
  timed(s, x, R_CTRL, CTRL_BASE | CTRL_TX_EN);
  ne = idx(s, x + mulp(s, LEAD_SYMS + pay) + (uint32_t)s->ustar);
  n0 = ne - (int32_t)pay;
  off = sixteenths(s, 4 * (C4_LAG4 + q)) - s->ubar;
  sent = pre;
  for (k = 0; k < pay; k++) {
    unsigned want = k < nfs ? bit(sz, k) : 1u;
    uint32_t at = tick(s, n0 + (int32_t)k) + (uint32_t)off;
    if (want != cur) {
      timed(s, at, R_TONE, G(want ? C4_GAIN : C4_INNER));
      s->w++;
      cur = want;
    } else if (sent < nw && sent < (k >> 4) + pre && (k + 1 >= nfs || bit(sz, k + 1) == cur)) {
      /* a word in the slack after this edge (it ends before the next one's write would start) */
      if ((int32_t)(at - now(&s->c)) < 0) s->late++;
      wait_to(api, &s->c, at);
      api->fd_write(R_FIFO, fword(f0, nwf, sent) ^ inv);
      sent++;
    }
  }
  if ((r = done(s, tick(s, ne) - mulp(s, 3), &e)) < 0) return r;
  if (r) {
    int32_t d = (int32_t)(e - tick(s, ne)), h = (int32_t)(s->p12 >> 13);
    if (d > h || d < -h) s->miss++;                       /* off by more than half a symbol */
    if (!s->rsync) fit_add(s, e, ne, 1);
  }
  return sent < nw ? -9 : 0;
}

/* rate packet j: P_WORDS words, P_PRE of them in the FIFO at the enable, the almost-empty flag at
   P_T words; each flag refills P_CHUNK words, so it comes back every 16 x P_CHUNK chip symbols,
   on the chip's clock. Polled back to back like done(): each flag's time between the last read
   that saw REG_0C low and the first that saw it high (a read is 0.56 symbol on the radio, so the
   polls start at a dithered time and the fit averages the brackets: about 80 ppm a packet on
   the radio, emulated, with 44 marks; 108 now). After the last refill only TX done. Then the
   line through the marks: rq[j], the symbol less P12_0 in Q12 cycles; tc, its centre; and bit j
   of rok if every mark came. */
static int probe(c4_t *s, uint32_t j) {
  const k6_api_t *api = s->api;
  uint32_t sent = P_PRE, x, prev, t, lim, i, rl, m0 = 0;
  uint32_t per = mulp(s, 16u * P_CHUNK);
  int32_t sxy = 0, sy = 0, q;
  int r, n = 0, whole = 0;
  if ((r = arm(s, P_WORDS, P_PRE, 0, 0, 0)) != 0) return r;
  api->fd_write(R_IRQ_EN, IRQ_TX_DONE | IRQ_AE);
  x = now(&s->c) + s->len + 96u * 50u;
  timed(s, x, R_CTRL, CTRL_BASE | CTRL_TX_EN);
#if C4_FIFOPROBE
  s->px[j] = x;
  s->pn[j] = s->pbad[j] = 0;
  s->pb[j] = 0;
  s->pr[j] = -9;
  s->p0[j] = s->pe[j] = x;
#endif
  lim = x + mulp(s, LEAD_SYMS + 16u * (P_PRE - P_T) + 64u);
  rl = s->len;                          /* a poll's length, until one is timed */
  prev = now(&s->c);
  for (;;) {
    t = now(&s->c);
    if (t - prev > s->rmax) s->rmax = t - prev;
    if (api->fd_read(R_STATUS) & 1) {
      uint32_t mk = prev + ((t - prev) >> 1);
      uint16_t b;
      api->fd_write(R_IRQ, 0);
      b = api->fd_read(R_IRQ);
#if C4_FIFOPROBE
      s->pb[j] |= b;
#endif
      if (sent < P_WORDS && !(b & IRQ_TX_DONE)) {
        for (i = 0; i < P_CHUNK && sent < P_WORDS; i++, sent++)
          api->fd_write(R_FIFO, fword(0, 0, sent & 15u));
        if (sent >= P_WORDS) api->fd_write(R_IRQ_EN, IRQ_TX_DONE);
        api->fd_write(R_IRQ, 0);          /* a flag that came back before the refill */
      }
      if (b & IRQ_TX_DONE) {
#if C4_FIFOPROBE
        s->pe[j] = mk;
        s->pr[j] = 0;
#endif
        whole = n == NM;
        break;
      }
      if (!(b & IRQ_AE) || n >= NM) {
#if C4_FIFOPROBE
        s->pbad[j]++;
#endif
        n = NM + 1;                       /* not a clean run: no rate from it */
      } else {
        int32_t d;
        if (!n) m0 = mk;
        d = (int32_t)(mk - m0 - mulp(s, 16u * P_CHUNK * (uint32_t)n));
        sxy += (2 * n - (NM - 1)) * d;
        sy += d;
#if C4_FIFOPROBE
        s->p0[j] = m0;
        d >>= 4;
        s->pm[j][n] = (int16_t)(d > 32767 ? 32767 : d < -32768 ? -32768 : d);
        s->pn[j] = (uint8_t)(n + 1);
#endif
        n++;
      }
      lim = mk + (sent < P_WORDS ? 2u * per : mulp(s, 16u * (P_T + P_CHUNK) + 64u));
      if (sent < P_WORDS) {
        /* the next flag's polls start 2 to 3 reads early, by eighths of a read in a scrambled
           turn: a fixed start would bracket every flag at the same phase (the refill takes the
           same time each), and the fit would keep that bias instead of averaging it away */
        uint32_t ahead = 3u * rl - (((uint32_t)n * 5u & 7u) * rl >> 3);
        if ((int32_t)(mk + per - ahead - now(&s->c)) > 0) wait_to(api, &s->c, mk + per - ahead);
      }
      prev = now(&s->c);
      continue;
    }
    rl = t - prev;
    prev = t;
    if ((int32_t)(t - lim) > 0) break;
  }
  api->fd_write(R_IRQ, 0);
  api->fd_write(R_CTRL, CTRL_BASE);
  /* the slope: sxy / SXX cycles a half mark (x = 2k - 107), so x 2 / 16 symbols x 4096 = Q12;
     the centre: mark 53.5, 856 symbols on from the first, at the line's mean */
  q = sdivq(sxy, SXX, 9);
  s->rq[j] = (int32_t)(s->p12 - P12_0) + q;
  s->tc = m0 + mulp(s, 856u) + (uint32_t)sdivq(sy, NM, 0);
  if (whole && q < RATE_MAX && q > -RATE_MAX) s->rok |= 1u << j;
  return keyed(api);
}

/* a rate packet just before a long one: the grid from its marks, the chip's rate as it is now
   (the fit's spans seconds), the phase from its centre mark plus phi, which the first one takes
   against the fit's grid (the done ticks, where the lag was found). A packet that didn't run
   whole leaves the grid as it was. */
static int resync(c4_t *s, uint32_t j) {
  int r;
  if ((r = probe(s, j)) != 0) return r;
  if (!(s->rok >> j & 1u)) return 0;
  if (!s->rsync) {
    s->phi = (int32_t)(tick(s, idx(s, s->tc)) - s->tc);
    s->rsync = 1;
  }
  s->p12 = (uint32_t)((int32_t)P12_0 + s->rq[j]);
  s->ta = s->tc + (uint32_t)s->phi;
  s->na = 0;
  return 0;
}

/* every packet in turn: 0, or a negative error */
static int burst(c4_t *s) {
  const k6_api_t *api = s->api;
  uint32_t k;
  int r;
  api->fd_write(R_IRQ_EN, 0);
  api->fd_write(R_CTRL, CTRL_BASE);
  api->fd_write(R_CFG, C4_CFG);
  api->fd_write(0x72, C4_REG72);
  api->fd_write(0x5A, 0x5555);          /* the sync word: the preamble's alternation goes on */
  api->fd_write(0x5B, 0x5555);
  api->fd_write(0x5C, 0x5625);          /* the chip's CRC off */
  s->len = 0;
  for (k = 0; k < 3; k++) {
    uint32_t t0 = now(&s->c), t1;
    api->fd_write(R_TONE, G(C4_GAIN));
    t1 = now(&s->c) - t0;
    if (t1 > s->len) s->len = t1;
  }
  s->wmin = s->wmax = s->len;
  if (s->len > WR_MAX) return -8;
  {
    uint16_t v5e = api->fd_read(0x5E);
    api->fd_write(0x5E, (uint16_t)((v5e & ~0x03F8u) | (P_T << 3)));
  }
  /* the MCU's clock against the chip's first (11-18: 1500 ppm off, where it was 520; the fit
     can't pull in that far from C4_PPM): the mean of the rate packets that ran whole */
  for (k = 0; k < NR; k++) {
    if (api->key_down()) return -4;
    if (C4_FIFOPROBE && k && (r = until(api, api->ms() + GAP_MS)) != 0) return r;
    if ((r = probe(s, k)) != 0) return r;
  }
  {
    int32_t sum = 0;
    uint32_t m = 0;
    for (k = 0; k < NR; k++)
      if (s->rok >> k & 1u) sum += s->rq[k], m++;
    if (m) s->p12 = (uint32_t)((int32_t)P12_0 + sdivq(sum, m, 0));
    s->rppm = ppm10(s);
  }
  if (C4_FIFOPROBE) return 0;
  for (k = 0; k < NCAL; k++) {
    if (api->key_down()) return -4;
    if ((r = cal(s, k)) != 0) return r;
  }
  if ((r = settle(s)) != 0) return r;
  s->cppm = ppm10(s);
  s->settled = 1;
  for (k = 0; k < NA; k++) {
    if (api->key_down()) return -4;
    if ((r = data(s, k & 3u, 1, A_FIRST + (int32_t)k, 0)) != 0) return r;
  }
  for (k = 0; k < NB; k++) {
    if (api->key_down()) return -4;
    if (GAP_MS && k && (r = until(api, api->ms() + GAP_MS)) != 0) return r;
    if ((r = resync(s, NR + k)) != 0) return r;
    if ((r = data(s, BSET[k] * C4_NFRAMES, C4_NFRAMES, BQ[k], BINV[k] ? 0xFFFFu : 0u)) != 0) return r;
  }
  return 0;
}

/* the FSK modem off (as Aura's exit_fsk_mode), the tone oscillator off */
static void quiet(const k6_api_t *api) {
  api->fd_write(R_CTRL, CTRL_BASE);
  api->fd_write(R_IRQ_EN, 0);
  api->fd_write(R_CFG, 0);
  api->fd_write(R_TONE, 0);
  api->fd_write(R_TONE1, 0);
}

/* When to key: the upload's carrier gone (C4_CLEAR_MS below C4_CLEAR_DBM), then at least one
   carrier of the job's own (its trigger: the Pluto's short PSK31 send, or the uploader's closing
   ID), then C4_PRE_MS with none, the time the Pluto's capture takes to start (a capture can't run
   while the Pluto sends, and the uploader holds it until its report wait is over). 0, -4 a key,
   -7 none of it within C4_WAIT_MS. */
static int trigger(const k6_api_t *api) {
  uint32_t quiet = api->ms();
  int gone = 0, heard = 0;
  for (;;) {
    uint32_t t = api->ms();
    if (KM_RSSI_DBM(api->fd_read(0x67)) >= C4_CLEAR_DBM) {
      if (gone && !heard) {
        heard = 1;
        api->say("c4 trigger heard");
      }
      quiet = t;
    }
    if (!gone && t - quiet >= C4_CLEAR_MS) gone = 1;
    if (heard && t - quiet >= C4_PRE_MS) return 0;
    if (t > C4_WAIT_MS) return -7;
    if (api->key_down()) return -4;
    api->delay_us(10000);
  }
}

/* the reports, after the key-up (nothing is said while keyed) */
static void report(c4_t *s, int cal_done, char *line) {
  const k6_api_t *api = s->api;
  char *p;
  int i;
  if (cal_done) {
    p = put(line, "c4lock cal ppm=");
    p = sdec(p, s->cppm);
    p = put(p, " th=");
    p = dec(p, s->th);
    p = put(p, "/16 u=");
    p = sdec(p, pct(s, s->ustar));
    p = put(p, " ub=");
    p = sdec(p, pct(s, s->ubar));
    *p = 0;
    api->say(line);
    p = put(line, "c4lock v=");
    for (i = 0; i < 8; i++) {
      if (i) *p++ = ' ';
      p = sdec(p, pct(s, s->v[i] - s->v[0]));
    }
    *p = 0;
    api->say(line);
  }
  p = put(line, "c4lock rate ppm=");
  p = sdec(p, s->rppm);
  p = put(p, " ok=");
  for (i = 0; i < (C4_FIFOPROBE ? NR : NR + NB); i++) *p++ = (char)('0' + (s->rok >> i & 1u));
  p = put(p, " phi=");
  p = sdec(p, pct(s, s->phi));
  *p = 0;
  api->say(line);
  p = put(line, "c4lock rq");
  for (i = 0; i < (C4_FIFOPROBE ? NR : NR + NB); i++) {
    *p++ = ' ';
    p = sdec(p, sdivq(s->rq[i] * 125, 1024, 0) + C4_PPM * 10);
  }
  *p = 0;
  api->say(line);
#if C4_FIFOPROBE
  for (i = 0; i < NR; i++) {
    int k;
    p = put(line, "c4fifo p=");
    p = dec(p, (uint32_t)i);
    p = put(p, " x=");
    p = dec(p, s->px[i]);
    p = put(p, " m0=");
    p = sdec(p, (int32_t)(s->p0[i] - s->px[i]));
    p = put(p, " e=");
    p = sdec(p, (int32_t)(s->pe[i] - s->px[i]));
    *p = 0;
    api->say(line);
    p = put(line, "c4fifo p=");
    p = dec(p, (uint32_t)i);
    p = put(p, " n=");
    p = dec(p, s->pn[i]);
    p = put(p, " bad=");
    p = dec(p, s->pbad[i]);
    p = put(p, " b=");
    p = dec(p, s->pb[i]);
    p = put(p, " r=");
    p = sdec(p, s->pr[i]);
    *p = 0;
    api->say(line);
    for (k = 0; k < s->pn[i]; k += 7) {   /* 7 a line: "c4fifo d3.42" and 7 x " -32768" fit the 60-char say */
      int m;
      p = put(line, "c4fifo d");
      p = dec(p, (uint32_t)i);
      p = put(p, ".");
      p = dec(p, (uint32_t)k);
      for (m = k; m < k + 7 && m < s->pn[i]; m++) {
        *p++ = ' ';
        p = sdec(p, s->pm[i][m]);
      }
      *p = 0;
      api->say(line);
    }
  }
#endif
  p = put(line, "c4lock w=");
  p = dec(p, s->w);
  p = put(p, " late=");
  p = dec(p, s->late);
  p = put(p, " miss=");
  p = dec(p, s->miss);
  p = put(p, " lost=");
  p = dec(p, s->lost + s->odd);
  p = put(p, " wr=");
  p = dec(p, s->wmin);
  p = put(p, "-");
  p = dec(p, s->wmax);
  p = put(p, " rd=");
  p = dec(p, s->rmax);
  *p = 0;
  api->say(line);
  if (s->nfit) {
    p = put(line, "c4lock fit ppm=");
    p = sdec(p, ppm10(s));
    p = put(p, " res=");
    p = sdec(p, pct(s, s->res));
    *p = 0;
    api->say(line);
  }
}

static int tx_main(const k6_api_t *api, ui_t *ku) {
  char line[64], *p;
  c4_t s;
  uint32_t t, i;
  int r = 0, id = 0;
  s.api = api;
  /* (an initialiser calls memclr) */
  s.w = s.late = s.wmin = s.wmax = s.len = s.miss = s.lost = s.odd = s.rmax = s.nfit = s.put = 0;
  s.th = 0;
  s.res = s.ustar = s.ubar = s.cppm = 0;
  s.settled = 0;
  s.rok = s.rsync = s.tc = 0;
  s.phi = 0;
  s.rppm = C4_PPM * 10;
  for (i = 0; i < NR + NB; i++) s.rq[i] = 0;
  s.p12 = P12_0;
  s.ta = 0;
  s.na = 0;
  for (i = 0; i < NCAL; i++) s.cok[i] = 0;
  if (api->level < 3) return -1;
  p = put(line, "c4lock n=");
  p = dec(p, NPKT);
  p = put(p, " g=");
  p = dec(p, C4_GAIN);
  p = put(p, "/");
  p = dec(p, C4_INNER);
  p = put(p, " lag=");
  p = dec(p, C4_LAG4);
  p = put(p, " ppm=");
  p = sdec(p, C4_PPM);
  p = put(p, " pa=");
  p = dec(p, C4_PA);
  p = put(p, " rx=");
  p = dec(p, api->rx_hz());
  *p = 0;
  api->say(line);
  if (api->rx_hz() != C4_HZ) return -2;
  if ((r = trigger(api)) != 0) return r;
  for (i = 0; i < NSAVED; i++) s.sv[i] = api->fd_read(SAVED[i]);
  s.r40 = s.sv[NSAVED - 1];
  ui_text(ku, 1, "ON THE AIR");
  ui_now(ku);
  if (api->tx_on(TX_MS) != 0) return -3;
  if (!C4_PA) api->tx_pa(0);
  quiet(api);
  clk_on(&s.c);
  t = api->ms();
  r = until(api, t + C4_LEAD_MS);
  if (!r) r = burst(&s);
  clk_off(&s.c);
  api->fd_write(R_DEV, s.r40);
  quiet(api);
  t = api->ms() - t - C4_LEAD_MS;
  if (!r || r == -8 || r == -9 || r == -10) {
    if (!r) r = until(api, api->ms() + 400);  /* a gap, then the ID */
    else until(api, api->ms() + 400);
    id = mcw(api);
    if (id < 0) r = id;
  }
  quiet(api);
  api->tx_off();
  /* the receive set-up back as it was (the core redoes the pager's own on return) */
  for (i = 0; i < NSAVED; i++) {
    if (SAVED[i] == R_CTRL) {
      api->fd_write(R_CTRL, (uint16_t)(s.sv[i] | CTRL_CLR));
      api->fd_write(R_CTRL, s.sv[i]);
    } else {
      api->fd_write(SAVED[i], s.sv[i]);
    }
  }
  report(&s, (int)s.settled, line);
  if (r) return r;
  p = put(line, "c4lock sent n=");
  p = dec(p, NPKT);
  p = put(p, " ms=");
  p = dec(p, t);
  p = put(p, " id=");
  p = dec(p, (uint32_t)id);
  *p = 0;
  api->say(line);
  return (int)NPKT;
}

int k6_main(const k6_api_t *api) {
  ui_t u;
  int r;
  ui_begin(&u, api, "C4FM YSF LOCK 440.850", "WAITING FOR QUIET");
  ui_text(&u, 5, "ANY KEY: STOP");
  ui_now(&u);
  r = tx_main(api, &u);
  ui_tx_end(&u, r, C4_HZ, "YSF SENT + CW ID");
  return r;
}
