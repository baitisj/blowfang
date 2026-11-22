/* micalias: does micsotto's converter fold high tones into the voice band? (2026-11-22,
   docs/k6-mic-probe.md). micsotto takes its 48 converter turns in one ~30 us burst every 125 us
   (TIM17's interrupt), so its second-order count barely filters 7-9 kHz (-1.5 dB at 7 kHz, by
   pa11adc.h's model), and an 8 kHz sample folds 7 kHz to 1 kHz. mickrec's continuous count (about
   200 turns a sample) rejects that band by 34-38 dB. This records the same mic, cable out, keyed
   with the PA off, three ways back to back, each paced by TIM17 (polled, no interrupt):
     A  8 kHz, 48 turns at the start of each period: micsotto's sampling
     B  32 kHz, 12 turns a period, kept raw: what reaches PA11 up to 16 kHz (decimated by
        tools/k6_micalias.py, it is also the 4 x 12 turns at 32 kHz fix)
     C  8 kHz, CONT_W turns filling the period: mickrec's continuous sampling
   for three sounds at the mic, each started by a key: a 1000 Hz tone (the reference), a 7000 Hz
   tone (the fold), and quiet (the hiss). A phone's tone generator held at the mic is enough.

   The screen runs it: PULL CABLE / THEN ANY KEY; PLAY 1000 HZ / THEN ANY KEY (recording, keyed
   with the PA off, ~1 s); PLAY 7000 HZ / THEN ANY KEY; SILENCE / THEN ANY KEY; a CW ID (AG7EW) at
   full power on the bench frequency; PLUG CABLE IN / THEN ANY KEY; then the lines
     micalias start 30=bff1 7d=e95c n=1924
     ma 1A hz=8000 w=48 g=128 n=1924 ms=240 rest=0123 over=0000      (a segment: its sound 1-3,
     p 0000 0123...                                                    way A-C, IMA ADPCM as mickrec)
     micalias done e=0
   The PA is checked off (REG_36) before and after each segment (at most 0.25 s). Writes, all put
   back: REG_30, REG_47, REG_48, REG_7D, PA11's mode, speed and output bit, PB2, TIM17 and its clock
   enable. Returns the samples kept, or -2 (not on the bench frequency), -3 (tx_on refused), -4 (the
   bench frequency busy), -5 (the PA would not go off, or REG_30 did not take the bits), -7 (no key),
   -8 (scratch), -12 (TIM17 in use). Level 13.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 13
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "m0rt.h"
#include "micadc.h"
#include "pa11adc.h"
#include "cableout.h"
K6_MODULE_NAME("MIC ALIAS");

#ifndef BENCH_HZ
#define BENCH_HZ 440850000u
#endif
#ifndef PRE
#define PRE 1100u /* way A's samples before the ones kept, after keying: the coupling capacitor settles */
#endif
#ifndef PRE_SEG
#define PRE_SEG 256u /* ways B and C: the count and the running mean settle */
#endif
#ifndef MAX_N
#define MAX_N 4004u /* way A's and C's samples a sound (B keeps twice as many); a multiple of 52 */
#endif
#ifndef KEY_WAIT_MS
#define KEY_WAIT_MS 1200000u
#endif
#ifndef SETTLE_MS
#define SETTLE_MS 700u /* after the key: its click gone */
#endif
#ifndef CONT_W
#define CONT_W 176u /* turns that fill 125 us (about 1.6 M turns a second at hold 3) */
#endif
#define SEL 4u
#define MIC_SENS 31u
#define HOLD 3u
#define AF_DAC 0x0200u
#define AMP (1u << 2)
#define RSSI_ON -90
#define CW_TEXT "AG7EW"
#define UNIT_US 60000u
#define TX_MS 20000u

#define RCC_APB2ENR 0x40021018u
#define TIM17EN (1u << 18)
#define TIM17 0x40014800u

static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }
static void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hexn(char *p, uint32_t v, int n) {
  const char *x = "0123456789abcdef";
  int i;
  for (i = 4 * (n - 1); i >= 0; i -= 4) *p++ = x[(v >> i) & 15];
  return p;
}

static char *dec(char *p, uint32_t v) {
  char t[10];
  int n = 0;
  do {
    t[n++] = (char)('0' + v % 10u);
    v /= 10u;
  } while (v);
  while (n) *p++ = t[--n];
  return p;
}

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }

static int code_of(char c) {
  static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c) return i;
  return 0;
}

static int hold(const k6_api_t *api, int on, int n) {
  if (api->tx_pa(on) < 0) return -5;
  while (n--) api->delay_us(UNIT_US);
  return 0;
}

/* the ID: AG7EW in CW at full power on the bench frequency, keyed down after */
static int call(const k6_api_t *api) {
  const char *s;
  int e;
  if (rssi(api) >= RSSI_ON) return -4;
  if (api->tx_on(TX_MS) != 0) return -3;
  e = hold(api, 0, 5);
  for (s = CW_TEXT; *s && !e; s++) {
    int cd = code_of(*s), b;
    for (b = 31; !(cd >> b); b--) {}
    while (b-- && !e) {
      e = hold(api, 1, (cd >> b) & 1 ? 3 : 1);
      if (!e) e = hold(api, 0, b ? 1 : 3);
    }
  }
  api->tx_off();
  return e;
}

/* IMA ADPCM, the standard coder (as mickrec) */
typedef struct {
  int32_t pred, index;
} ima_t;

static const uint16_t STEPS[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,   28,
    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,  118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,  494,
    544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878, 2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845, 8630,
    9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};

static uint32_t ima_code(ima_t *s, int32_t x) {
  int32_t step = STEPS[s->index], d = x - s->pred, q = step >> 3;
  uint32_t c = 0;
  if (d < 0) {
    c = 8;
    d = -d;
  }
  if (d >= step) {
    c |= 4;
    d -= step;
    q += step;
  }
  if (d >= step >> 1) {
    c |= 2;
    d -= step >> 1;
    q += step >> 1;
  }
  if (d >= step >> 2) {
    c |= 1;
    q += step >> 2;
  }
  s->pred += c & 8 ? -q : q;
  if (s->pred > 32767) s->pred = 32767;
  if (s->pred < -32768) s->pred = -32768;
  s->index += (c & 4) ? (int32_t)(2 * (c & 3)) + 2 : -1;
  if (s->index < 0) s->index = 0;
  if (s->index > 88) s->index = 88;
  return c;
}

/* a way: its period in TIM17 counts (96 MHz), turns a sample, gain (x = gain * (y - mean)) */
typedef struct {
  uint16_t per, w, g, k;
} way_t;
static const way_t WAYS[3] = {{12000, 48, 128, 1}, {3000, 12, 2048, 2}, {12000, CONT_W, 8, 1}};

typedef struct {
  uint32_t n, ms, rest, over;
} seg_t;

/* k * n samples of one way into rec (4 bits each): PA11 read at the start of each TIM17 period */
static void segment(const k6_api_t *api, uint32_t m_in, const way_t *wy, uint32_t pre, uint32_t n, uint8_t *rec,
                    seg_t *o) {
  pa11_t c = {0, 0, 0, 0};
  ima_t ima = {0, 0};
  int32_t mean = 0;
  uint32_t i, j, t0 = 0;
  o->n = n;
  o->over = 0;
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x28, 0);              /* PSC */
  wr(TIM17 + 0x2C, wy->per - 1u);   /* ARR */
  wr(TIM17 + 0x14, 1);              /* UG */
  wr(TIM17 + 0x10, 0);
  wr(TIM17 + 0x00, 1);
  for (i = 0; i < pre + n; i++) {
    int32_t y, x;
    uint32_t code;
    while (!(rd(TIM17 + 0x10) & 1u)) {
    }
    wr(TIM17 + 0x10, 0);
    y = (int32_t)pa11_sample(m_in, HOLD, wy->w, &c) << 5;
    if (i < 3) mean = y;
    mean += (y - mean) >> 5;
    if (i < pre) continue;
    if (i == pre) {
      t0 = api->ms();
      o->rest = (uint32_t)mean >> 5;
    }
    x = ((y - mean) * (int32_t)wy->g) >> 5;
    if (x > 32767 || x < -32768) {
      x = x < 0 ? -32768 : 32767;
      o->over++;
    }
    code = ima_code(&ima, x);
    j = i - pre;
    if (j & 1u) rec[j >> 1] |= (uint8_t)(code << 4);
    else rec[j >> 1] = (uint8_t)code;
  }
  o->ms = api->ms() - t0;
  wr(TIM17 + 0x00, 0);
}

static void show(const k6_api_t *api, const char *a, const char *b) { co_show(api, "MIC ALIAS CABLE OUT", a, b); }

int k6_main(const k6_api_t *api) {
  static const char SOUND[3][13] = {"PLAY 1000 HZ", "PLAY 7000 HZ", "SILENCE"}; /* (no pointers: -fropi) */
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           m_in = moder & ~(3u << (2 * PA11_PIN)), room = 0, apb2 = rd(RCC_APB2ENR), n, s30 = 0, want, i, j, k, t,
           off, len;
  uint16_t r47 = api->fd_read(0x47), r48 = api->fd_read(0x48), r7d = api->fd_read(0x7D);
  char *line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u), *p;
  uint8_t *rec = (uint8_t *)line + 64;
  seg_t segs[9];
  int ret = 0, e = 0;
  if (api->level < 6) return -1;
  if (room < 64 + 4 + 3 * 2 * 52 * 2) return -8;
  n = (room - 68) / 6u; /* bytes: 3 sounds x (n + 2n + n) / 2 */
  n -= n % 52u;
  if (n > MAX_N) n = MAX_N;
  p = hexn(put(line, "micalias start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 7d="), r7d, 4);
  p = dec(put(p, " n="), n);
  *p = 0;
  api->say(line);
  if (home != BENCH_HZ) return -2;
  if (apb2 & TIM17EN) return -12;
  for (i = 0; i < 9; i++) segs[i].n = 0;
  wr(MIC_GPIOB + MIC_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  show(api, "PULL CABLE", "THEN ANY KEY");
  if (co_key(api, KEY_WAIT_MS) < 0) {
    ret = -7;
    goto out;
  }
  for (t = 0, off = 0; t < 3 && !e; t++) {
    show(api, SOUND[t], "THEN ANY KEY");
    if (co_key(api, KEY_WAIT_MS) < 0) {
      e = -7;
      break;
    }
    show(api, SOUND[t], "RECORDING");
    api->delay_us(SETTLE_MS * 1000u);
    /* keyed with the PA off, the mic on (mickrec's record()) */
    if (rssi(api) >= RSSI_ON) e = -4;
    else if (api->tx_on(TX_MS) != 0) e = -3;
    else if (api->tx_pa(0) < 0) e = -5;
    if (!e) {
      s30 = api->fd_read(0x30);
      if (api->fd_read(0x36) & 0x0080u) e = -5;
    }
    want = (s30 | AF_DAC | MIC_ADC) & 0xFFFFu;
    if (!e && api->fd_read(0x30) != want && api->fd_write(0x30, (uint16_t)want) != 0) mic_bus_write(api, 0x30, want);
    if (!e && api->fd_read(0x30) != want) e = -5;
    if (!e) {
      api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (SEL << 8)));
      api->fd_write(0x48, (uint16_t)(r48 | 0x03F0u));
      api->fd_write(0x7D, (uint16_t)((r7d & ~0x001Fu) | MIC_SENS));
      api->delay_us(20000);
      wr(RCC_APB2ENR, apb2 | TIM17EN);
    }
    for (k = 0; k < 3 && !e; k++) {
      len = WAYS[k].k * n;
      segment(api, m_in, &WAYS[k], k ? PRE_SEG : PRE, len, rec + off, &segs[3 * t + k]);
      off += len / 2u;
      if (api->fd_read(0x36) & 0x0080u) e = -5; /* the PA still off */
    }
    wr(RCC_APB2ENR, apb2); /* (each segment stops TIM17 itself) */
    if (s30 && api->fd_read(0x30) != s30 && api->fd_write(0x30, (uint16_t)s30) != 0) mic_bus_write(api, 0x30, s30);
    api->fd_write(0x47, r47);
    api->fd_write(0x48, r48);
    api->fd_write(0x7D, r7d);
    api->tx_off();
  }
  if (segs[0].n) { /* the ID after any keying */
    show(api, "QUIET PLEASE", "CW ID");
    api->delay_us(500000);
    call(api);
  }
  ret = e ? e : (int)(4u * n * t);
out:
  api->tx_off();
  api->fd_write(0x47, r47);
  api->fd_write(0x48, r48);
  api->fd_write(0x7D, r7d);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  if (ret != -7) {
    show(api, "PLUG CABLE IN", "THEN ANY KEY");
    co_key(api, KEY_WAIT_MS);
    show(api, "SENDING", "");
    api->delay_us(1000000);
  }
  for (i = 0, off = 0; i < 9 && segs[i].n; i++) {
    const way_t *wy = &WAYS[i % 3];
    p = put(line, "ma ");
    *p++ = (char)('1' + i / 3);
    *p++ = (char)('A' + i % 3);
    p = dec(put(p, " hz="), 96000000u / wy->per);
    p = dec(put(p, " w="), wy->w);
    p = dec(put(p, " g="), wy->g);
    p = dec(put(p, " n="), segs[i].n);
    p = dec(put(p, " ms="), segs[i].ms);
    p = hexn(put(p, " rest="), segs[i].rest, 4);
    p = hexn(put(p, " over="), segs[i].over, 4);
    *p = 0;
    api->say(line);
    for (j = 0; j < segs[i].n / 2u; j += 26) {
      p = hexn(put(line, "p "), j, 4);
      *p++ = ' ';
      for (k = j; k < j + 26; k++) p = hexn(p, rec[off + k], 2);
      *p = 0;
      api->say(line);
      api->delay_us(15000);
    }
    off += segs[i].n / 2u;
  }
  p = put(line, "micalias done e=");
  if (e < 0) *p++ = '-';
  p = dec(p, (uint32_t)(e < 0 ? -e : e));
  *p = 0;
  api->say(line);
  return ret;
}
