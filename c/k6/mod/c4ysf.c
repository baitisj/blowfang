/* c4ysf: the K6 sends YSF (C4FM, Yaesu framing) from its own FD6818, run 2 of docs/k6-c4fm.md.
   Run 1 (c4probe, 2026-11-18 01:32Z) showed the way: the FSK modem at 4800 baud sends each
   symbol's sign, and REG_70's tone-2 gain, rewritten on the symbol edges, sets its size (live
   mid-packet; the chip's symbols run about 1.75 symbols behind the write). This module sends a
   whole YSF transmission (header, data FN 0, FN 1 "AG7EW K6 C4FM", terminator) at the YSF
   deviation, first as one frame a packet, then back to back in one 240-byte packet, the way a
   C4FM radio needs it:
     A0..A3  one frame a packet, C4_GAIN / C4_INNER (2.7 / 0.9 kHz from run 1's levels), writes
             C4_OFF quarter symbols late (run 1's best, +7)
     B1      all four frames in one packet, the FIFO filled with all 120 words first: does the
             FIFO hold 120?
     B2      the same, 64 words first and the rest written as the chip takes them, in symbols that
             need no size write (the next symbol needs none either)
     B3      the same with 32 words first
   Like c4probe it keys C4_PRE_MS after the bench job's trigger carrier, on 440.850 only, the PA
   off by default, and ends with AG7EW in MCW. A Pluto capture goes to
   python3 -m modem.k6c4fm analyze CAP.wav --plan ysf.

   It says
     K6 MOD say c4ysf n=7 g=78/27 off=7 pa=0 rx=440850000
     K6 MOD say c4 trigger heard
     K6 MOD say c4ysf w=... f=... late=... wr=...-...      size writes, timed FIFO writes, late, a
                                                          write's length (cycles at 96 MHz)
     K6 MOD say c4ysf sent n=7 ms=... id=52
   and returns the packets sent, or a negative error as c4probe's (-8 writes too slow, -9 a
   packet never finished).

   Build: python -m modem.k6 mod out.wav c/k6/mod/c4ysf.c --api-level 3 --key-file KEY.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 3
#include "../k6_mod.h"
#include "k6ui.h"
#include "c4probe_frames.h"   /* C4_YSF_SIGN, C4_YSF_SIZE: the four frames, back to back */
#ifndef K6_NAME
#define K6_NAME "C4FM YSF TX"
#endif
K6_MODULE_NAME(K6_NAME);

#ifndef C4_HZ
#define C4_HZ 440850000u
#endif
#ifndef C4_PA
#define C4_PA 0
#endif
#ifndef C4_GAIN
#define C4_GAIN 78          /* REG_70 tone-2 gain for +-2.7 kHz (run 1: 48 -> 1434 Hz, 96 -> 3425) */
#endif
#ifndef C4_INNER
#define C4_INNER 27         /* +-0.9 kHz (run 1: 16 -> 731 Hz, 32 -> 973) */
#endif
#ifndef C4_OFF
#define C4_OFF 7            /* size writes, quarter symbols after the aim (run 1's best) */
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
#define LEAD_SYMS 160u                              /* 16 preamble bytes, 4 sync bytes */
#define FRAME_SYMS 480u
#define FRAME_WORDS 30u
#define WR_MAX (SYM * 3u / 4u)                      /* a write longer than this can't keep up */
#define NPKT 7
#define TX_MS (C4_LEAD_MS + 4u * 160u + 3u * 480u + 7u * 60u + 400u + ID_UNITS * UNIT_MS + 1500u)

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
  uint32_t w, f, late, wmin, wmax, len, short_;
  uint16_t sv[NSAVED];
  uint16_t r40;
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
  s->w++;
}

static unsigned bit(const uint8_t *b, uint32_t k) {
  return (b[k >> 3] >> (7u - (k & 7u))) & 1u;
}

/* One packet of nf frames from frame f0 (C4_YSF_SIGN / _SIZE), pre words in the FIFO before
   the enable and the rest written as the chip takes them (a word is 16 symbols; one goes in a
   symbol whose edge and the next need no size write, while the FIFO holds fewer than pre). 0, or
   a negative error. */
static int packet(c4_t *s, uint32_t f0, uint32_t nf, uint32_t pre) {
  const k6_api_t *api = s->api;
  const uint8_t *sg = C4_YSF_SIGN + f0 * C4_PKT_BYTES, *sz = C4_YSF_SIZE + f0 * C4_PKT_BYTES;
  uint32_t i, k, t0, base, nw = nf * FRAME_WORDS, nsym = nf * FRAME_SYMS, sent;
  unsigned cur = 1;
  int r;
  api->fd_write(R_IRQ_EN, IRQ_TX_DONE);
  api->fd_write(R_CTRL, CTRL_BASE | CTRL_CLR);
  api->fd_write(R_CTRL, CTRL_BASE);
  api->fd_write(0x5D, (uint16_t)((nf * C4_PKT_BYTES - 1u) << 8));
  if (pre > nw) pre = nw;
  for (i = 0; i < pre; i++) api->fd_write(R_FIFO, (uint16_t)(sg[2 * i] | sg[2 * i + 1] << 8));
  sent = pre;
  if ((r = until(api, api->ms() + 20)) != 0) return r;       /* as the stock MDC path */
  if ((r = keyed(api)) != 0) return r;
  api->fd_write(R_CTRL, CTRL_BASE | CTRL_TX_EN);
  t0 = now(&s->c);
  base = t0 + LEAD_SYMS * SYM + (uint32_t)(C4_OFF * (int32_t)(SYM / 4u));
  for (k = 0; k < nsym; k++) {
    unsigned want = bit(sz, k);
    if (want != cur) {
      timed(s, base + k * SYM, R_TONE, G(want ? C4_GAIN : C4_INNER));
      cur = want;
    } else if (sent < nw && sent < (k >> 4) + pre && (k + 1 >= nsym || bit(sz, k + 1) == cur)) {
      /* the word in the slack after this edge (aimed at the edge, so it ends before the next
         one's write would start) */
      uint32_t at = base + k * SYM;
      if ((int32_t)(at - now(&s->c)) < 0) s->late++;
      wait_to(api, &s->c, at);
      api->fd_write(R_FIFO, (uint16_t)(sg[2 * sent] | sg[2 * sent + 1] << 8));
      sent++;
      s->f++;
    }
  }
  wait_to(api, &s->c, t0 + (LEAD_SYMS + nsym + 2u) * SYM);    /* past the last symbol */
  api->fd_write(R_TONE, G(C4_GAIN));
  for (i = 0; i < 40 && !(api->fd_read(R_STATUS) & 1); i++) api->delay_us(1000);
  api->fd_write(R_IRQ, 0);
  api->fd_write(R_CTRL, CTRL_BASE);
  if (sent < nw) s->short_++;
  return i < 40 ? 0 : -9;
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
  for (k = 0; k < C4_NFRAMES; k++) {
    if (api->key_down()) return -4;
    if ((r = packet(s, k, 1, FRAME_WORDS)) != 0) return r;
  }
  if ((r = packet(s, 0, C4_NFRAMES, C4_NFRAMES * FRAME_WORDS)) != 0) return r;
  if (api->key_down()) return -4;
  if ((r = packet(s, 0, C4_NFRAMES, 64)) != 0) return r;
  if (api->key_down()) return -4;
  return packet(s, 0, C4_NFRAMES, 32);
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

static int tx_main(const k6_api_t *api, ui_t *ku) {
  char line[64], *p;
  c4_t s;
  uint32_t t, i;
  int r = 0, id = 0;
  s.api = api;
  s.w = s.f = s.late = s.wmin = s.wmax = s.len = s.short_ = 0;   /* (an initialiser calls memclr) */
  if (api->level < 3) return -1;
  p = put(line, "c4ysf n=");
  p = dec(p, NPKT);
  p = put(p, " g=");
  p = dec(p, C4_GAIN);
  p = put(p, "/");
  p = dec(p, C4_INNER);
  p = put(p, " off=");
  p = dec(p, C4_OFF);
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
  p = put(line, "c4ysf w=");
  p = dec(p, s.w);
  p = put(p, " f=");
  p = dec(p, s.f);
  p = put(p, " late=");
  p = dec(p, s.late);
  p = put(p, " wr=");
  p = dec(p, s.wmin);
  p = put(p, "-");
  p = dec(p, s.wmax);
  *p = 0;
  api->say(line);
  if (!r || r == -8 || r == -9) {
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
  if (r) return r;
  p = put(line, "c4ysf sent n=");
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
  ui_begin(&u, api, "C4FM YSF TX 440.850", "WAITING FOR QUIET");
  ui_text(&u, 5, "ANY KEY: STOP");
  ui_now(&u);
  r = tx_main(api, &u);
  ui_tx_end(&u, r, C4_HZ, "YSF SENT + CW ID");
  return r;
}
