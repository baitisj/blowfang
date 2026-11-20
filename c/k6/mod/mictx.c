/* mictx: does the mic reach anything the MCU can read while the chip is keyed? (2026-11-20, Jeff:
   "what if we ... modulated AM, deactivated finals, and sampled BUZZER at the end of the transmit
   chain?"). mickey (docs/k6-mic-probe.md) found the AF pin dead keyed: the chip's receive chain is
   off. The chip has no AM transmitter, so this tries the two ways left that need no hardware:
     - the mic level registers while the TX DSP runs: REG_64 (the voice amplitude VOX and the mic
       bar read), REG_6F (AF TX/RX amplitude), REG_63, REG_65, read back to back (an fd_read is
       about 117 us: 8.5 kHz) and every 2 ms (an envelope), with the mic ADC on and (control) off;
     - the receive chain switched on as well while keyed (REG_30 RX link bits 13-10 and the RX
       DSP, bit 0, beside the TX DSP), so whatever the transmitter makes reaches a demodulator:
       the AF pin through PA11's converter at the FM, raw baseband, USB, AM, FSK and bypass
       selects, and the registers again in that state.
   Keys up on the bench frequency (tx_on: the core's guards, low power), sends AG7EW in CW with the
   PA (tx_pa), then with the PA OFF makes 20 captures of up to 260 samples (in the scratch, dumped
   after key-down). Twice: the room quiet (tags 0..9, a..j), and (A..T) once a carrier has stayed
   5 s on SOUND_HZ: the bench sends a gated tone there, and the IC-7100, tuned there, plays it from
   its speaker beside the K6 (or, for the control build, the IC-7100 sits on another channel).

   The mic and the receive chain are never on the air: their bits are set only while REG_36 says
   the PA is off, and tx_pa(1) is not called after. REG_30 goes over the chip's bus pins (micadc.h)
   where fd_write refuses it; it is put back before key-down.

   Lines (under 60 characters; samples are four hex digits, 13 a line):
     mictx start 30=bff1 47=6040 rx=1a46e150
     mictx keyed q=0 30=c1fa 36=007f now=c3fe
     3 000 11f811e011f0...
     mictx 3 ticks=0092 k=1 a=64 m=1 rx=0 d=1 n=104      k: 0 the AF pin at select a, 1 register
                                                          a back to back, 2 every 2 ms; m mic ADC,
                                                          rx the receive chain, d the TX DSP
     mictx sound rssi=-4f            (or: mictx no sound)
     mictx done
   Returns the captures made, or -8 (scratch under 3 KB), -6 (a key), -5 (the PA would not go off,
   or REG_30 did not take the bits), -4 (the bench frequency is busy), -3 (tx_on refused), -2 (not
   on the bench frequency), -1 (no transmitter). Level 13 (the radio's: 15,408 B of block).
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 13
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "micadc.h"
#include "pa11adc.h"

#define AMP (1u << 2)
#ifndef SOUND_HZ
#define SOUND_HZ 445850000u
#endif
#ifndef N_MAX
#define N_MAX 260u /* 20 lines of 13 */
#endif
#ifndef PRE
#define PRE 2400u /* samples before the ones kept: the 1 uF coupling capacitor settles */
#endif
#ifndef QUIET_WAIT_MS
#define QUIET_WAIT_MS 30000u
#endif
#ifndef HOLD_MS
#define HOLD_MS 5000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 120000u
#endif
#ifndef BENCH_HZ
#define BENCH_HZ 440850000u
#endif
#define RSSI_ON -90 /* dBm: the sound's carrier is sent weak */
#define HOLD 3u
#define W 90u
#define N_CFG 20u
#define AF_DAC 0x0200u
#define CW_TEXT "AG7EW"
#define UNIT_US 60000u /* 20 wpm */
#define TX_MS 30000u
#define RXB 0x3C01u /* REG_30: the RX link (13-10) and the RX DSP (0) */
#define GAP_US 2000u

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

static char *dbm(char *p, int v) {
  if (v < 0) {
    *p++ = '-';
    v = -v;
  }
  return hexn(p, (uint32_t)v, 2);
}

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }

typedef struct {
  const k6_api_t *api;
  char *line; /* 64 bytes, in the scratch like buf */
  uint16_t *buf;
  uint32_t m_in, n;
} run_t;

/* Morse by code, as cwid.c: start at 1, then 0 for a dot and 1 for a dash (E = 2, A = 5) */
static int code_of(char c) {
  static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c) return i;
  return 0;
}

/* the captures: kind (0 AF pin, 1 register back to back, 2 register every GAP_US), select or
   register, REG_30 bits to set beside the keyed word and AF_DAC, bits to clear */
typedef struct {
  uint8_t kind, arg;
  uint16_t set, clr;
} cfg_t;
static const cfg_t CFG[N_CFG] = {
    {1, 0x64, MIC_ADC, 0},       {1, 0x6F, MIC_ADC, 0},       {2, 0x64, MIC_ADC, 0},       {2, 0x6F, MIC_ADC, 0},
    {1, 0x63, MIC_ADC, 0},       {1, 0x65, MIC_ADC, 0},       {2, 0x64, 0, MIC_ADC},       {2, 0x6F, 0, MIC_ADC},
    {0, 1, MIC_ADC | RXB, 0},    {0, 4, MIC_ADC | RXB, 0},    {0, 5, MIC_ADC | RXB, 0},    {0, 7, MIC_ADC | RXB, 0},
    {0, 8, MIC_ADC | RXB, 0},    {0, 9, MIC_ADC | RXB, 0},    {0, 1, MIC_ADC | RXB, MIC_DSP}, {0, 4, MIC_ADC | RXB, MIC_DSP},
    {1, 0x64, MIC_ADC | RXB, 0}, {1, 0x6F, MIC_ADC | RXB, 0}, {2, 0x64, MIC_ADC | RXB, 0}, {2, 0x6F, MIC_ADC | RXB, 0},
};

/* the PA on or off for n units; 0, -5 (the core keyed down) or -6 (a key) */
static int hold(const k6_api_t *api, int on, int n) {
  if (api->tx_pa(on) < 0) return -5;
  while (n--) {
    if (api->key_poll()) return -6;
    api->delay_us(UNIT_US);
  }
  return 0;
}

/* one keyed pass: the ID, then the 20 captures with the PA off, key-down, the dump.
   0, or a negative error (keyed down either way) */
static int pass(const run_t *r, const char *tags, uint32_t q, uint32_t r47, int *made) {
  const k6_api_t *api = r->api;
  char *line = r->line, *p;
  uint16_t ticks[N_CFG];
  uint32_t k, i, j, s30 = 0, now = 0;
  const char *s;
  int e;
  if (rssi(api) >= RSSI_ON) return -4; /* never onto a busy channel */
  e = api->tx_on(TX_MS);
  if (e != 0) return -3;
  e = hold(api, 0, 5);
  for (s = CW_TEXT; *s && !e; s++) {
    int c = code_of(*s), b;
    for (b = 31; !(c >> b); b--) {}
    while (b-- && !e) {
      e = hold(api, 1, (c >> b) & 1 ? 3 : 1);
      if (!e) e = hold(api, 0, b ? 1 : 3);
    }
  }
  if (!e && api->tx_pa(0) < 0) e = -5;
  if (!e) {
    s30 = api->fd_read(0x30);
    if (api->fd_read(0x36) & 0x0080u) e = -5; /* the PA is still on: no mic bit */
  }
  for (k = 0; k < N_CFG && !e; k++) {
    const cfg_t *c = &CFG[k];
    uint32_t t0 = 0, want = ((s30 | AF_DAC | c->set) & ~(uint32_t)c->clr) & 0xFFFFu;
    pa11_t pc = {0, 0, 0, 0};
    uint16_t *buf = r->buf + k * r->n;
    if (api->key_poll()) e = -6;
    if (api->fd_read(0x36) & 0x0080u) e = -5;
    if (e) break;
    if (api->fd_read(0x30) != want && api->fd_write(0x30, (uint16_t)want) != 0) mic_bus_write(api, 0x30, want);
    now = api->fd_read(0x30);
    if (now != want) {
      e = -5;
      break;
    }
    api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | ((c->kind ? 1u : c->arg) << 8)));
    api->delay_us(20000);
    if (c->kind) {
      t0 = api->ticks();
      for (i = 0; i < r->n; i++) {
        buf[i] = api->fd_read(c->arg);
        if (c->kind == 2) api->delay_us(GAP_US);
      }
    } else {
      for (i = 0; i < PRE + r->n; i++) {
        uint32_t y;
        if (i == PRE) t0 = api->ticks();
        y = pa11_sample(r->m_in, HOLD, W, &pc);
        if (i >= PRE) buf[i - PRE] = (uint16_t)y;
      }
    }
    ticks[k] = (uint16_t)(api->ticks() - t0);
  }
  /* the bits off again before key-down (tx_off writes the receive word whole anyway) */
  if (s30 && api->fd_read(0x30) != s30 && api->fd_write(0x30, (uint16_t)s30) != 0) mic_bus_write(api, 0x30, s30);
  api->fd_write(0x47, (uint16_t)r47);
  api->tx_off();
  p = hexn(put(line, "mictx keyed q="), q, 1);
  p = hexn(put(p, " 30="), s30, 4);
  p = hexn(put(p, " 36="), api->fd_read(0x36), 4);
  p = hexn(put(p, " now="), now, 4);
  *p = 0;
  api->say(line);
  if (e) return e;
  for (k = 0; k < N_CFG; k++) {
    uint16_t *buf = r->buf + k * r->n;
    for (i = 0; i < r->n; i += 13) {
      line[0] = tags[k];
      line[1] = ' ';
      p = hexn(line + 2, i, 3);
      *p++ = ' ';
      for (j = i; j < i + 13 && j < r->n; j++) p = hexn(p, buf[j], 4);
      *p = 0;
      api->say(line);
    }
    p = put(line, "mictx ");
    *p++ = tags[k];
    p = hexn(put(p, " ticks="), ticks[k], 4);
    p = hexn(put(p, " k="), CFG[k].kind, 1);
    p = hexn(put(p, " a="), CFG[k].arg, 2);
    p = hexn(put(p, " m="), !!(CFG[k].set & MIC_ADC), 1);
    p = hexn(put(p, " rx="), !!(CFG[k].set & RXB), 1);
    p = hexn(put(p, " d="), !(CFG[k].clr & MIC_DSP), 1);
    p = hexn(put(p, " n="), r->n, 3);
    *p = 0;
    api->say(line);
    api->delay_us(20000); /* the serial port drains */
    ++*made;
  }
  return 0;
}

int k6_main(const k6_api_t *api) {
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           room = 0, t0, t1, up = 0;
  uint16_t r47 = api->fd_read(0x47);
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  run_t r;
  char *line, *p;
  int ret = 0, e;
  if (api->level < 3) return -1;
  r.api = api;
  r.line = line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  r.buf = (uint16_t *)(line + 64);
  r.m_in = moder & ~(3u << (2 * PA11_PIN));
  if (room < 3072) return -8;
  for (r.n = N_MAX; 2 * N_CFG * r.n + 72 > room; r.n -= 13) {} /* (N_MAX: a multiple of 13) */
  p = hexn(put(line, "mictx start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 47="), r47, 4);
  p = hexn(put(p, " rx="), home, 8);
  *p = 0;
  api->say(line);
  if (home != BENCH_HZ) return -2;
  wr(MIC_GPIOB + MIC_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  /* the upload's tail: wait until the channel has been quiet for a second */
  t0 = api->ms();
  t1 = t0;
  while (api->ms() - t0 < QUIET_WAIT_MS && api->ms() - t1 < 1000u) {
    if (rssi(api) >= RSSI_ON) t1 = api->ms();
    if (api->key_poll()) {
      ret = -6;
      goto out;
    }
    api->delay_us(20000);
  }
  if ((e = pass(&r, "0123456789abcdefghij", 0, r47, &ret)) < 0) {
    ret = e;
    goto out;
  }
  /* the sound: a carrier on SOUND_HZ that stays HOLD_MS (its ID over, the tone on) */
  api->tune(SOUND_HZ, wide);
  t0 = api->ms();
  for (;;) {
    if (rssi(api) < RSSI_ON) {
      up = 0;
    } else if (!up) {
      up = 1;
      t1 = api->ms();
    } else if (api->ms() - t1 >= HOLD_MS) {
      break;
    }
    if (api->key_poll()) {
      ret = -6;
      goto out;
    }
    if (api->ms() - t0 >= WAIT_MS) {
      api->say("mictx no sound");
      goto out;
    }
    api->delay_us(20000);
  }
  *dbm(put(line, "mictx sound rssi="), rssi(api)) = 0;
  api->say(line);
  api->tune(home, wide);
  api->delay_us(300000);
  if ((e = pass(&r, "ABCDEFGHIJKLMNOPQRST", 1, r47, &ret)) < 0) ret = e;
out:
  api->tx_off();
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r47);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  api->say("mictx done");
  return ret;
}
