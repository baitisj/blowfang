/* afpin: can the beep pin read the FD6818's audio output? (2026-11-19, docs/k6-mic-probe.md)
   PA11 as a one-pin delta-sigma converter (pa11adc.h) on the chip's AF output, while the K6
   receives. Nothing is added to the radio, and the speaker amplifier is off for the run.

   Captures, each dumped whole as four hex digits a sample for tools/k6_micprobe.py:
     m      2,990 samples (a sixth of a second), AF output muted: the converter's own noise
     h      2,990, AF output = received audio with no signal: the receiver's hiss
     A..P   260 each: REG_47's AF output select at 0 .. 15, still with no signal (which selects
            put anything on the pin in receive, before any mic is switched on)
     t..y   2,990 each, 0.7 s apart, once a carrier has stayed on the channel for 20 s (the
            bench sends a tone a minute long; the uploader's own CW ID is shorter): drive times
            5, 3, 2 and 8, then 5 with twice the turns a sample, then 5 again. Skipped if no
            such carrier comes within 150 s.
   Lines (under 60 characters):
     afpin start 47=6040 48=b3a8 moder=28000000 pupdr=24000000
     m 000 11f811e011f0...            tag, first sample's index (hex), 13 samples
     afpin m ticks=0604 way=1 hold=3 w=5a rssi=-72 sel=0
                                      the capture's length in 100 us ticks, the converter (1:
                                      pa11adc.h's), its drive time, the turns a sample (a sample
                                      is 0 .. w * w), the channel's RSSI after it in dBm and
                                      REG_47's select (all hex)
     afpin carrier rssi=-3d           (or: afpin no carrier)
     afpin done
   Writes: REG_47 (the AF output select, [11:8]), put back; the MCU's PA11 mode, speed and
   output bit and PB2 (the amplifier), put back. Nothing transmits, nothing sounds. Returns the
   captures made, or -8 (scratch under 5,984 bytes), -6 (a key stopped it). Level 4 (key_poll).
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 4
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "pa11adc.h"

#define GPIOB 0x48000400u
#define G_BRR 0x28u
#define AMP (1u << 2)
#ifndef N
#define N 2990u      /* 230 lines of 13 */
#endif
#ifndef N_SEL
#define N_SEL 260u   /* 20 lines */
#endif
#ifndef PRE
#define PRE 2400u    /* samples before the ones kept: the 1 uF coupling capacitor settles */
#endif
#ifndef WAIT_MS
#define WAIT_MS 150000u
#endif
#ifndef HOLD_MS
#define HOLD_MS 20000u
#endif
#define RSSI_ON -85  /* dBm: the bench's carrier is far above it, the channel's floor far below */

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

typedef struct {
  uint8_t hold, w;
} set_t;

/* n samples into buf after PRE dropped; returns the n samples' length in 100 us ticks */
static uint32_t capture(const k6_api_t *api, uint16_t *buf, uint32_t n, uint32_t m_in, set_t s) {
  uint32_t i, t0 = 0;
  pa11_t c = {0, 0, 0, 0};
  for (i = 0; i < PRE + n; i++) {
    uint32_t y;
    if (i == PRE) t0 = api->ticks();
    y = pa11_sample(m_in, s.hold, s.w, &c);
    if (i >= PRE) buf[i - PRE] = (uint16_t)y;
  }
  return (api->ticks() - t0) & 0xFFFFu;
}

static void dump(const k6_api_t *api, char tag, const uint16_t *buf, uint32_t n, uint32_t ticks, set_t s) {
  char line[64], *p;
  uint32_t i, j;
  for (i = 0; i < n; i += 13) {
    line[0] = tag;
    line[1] = ' ';
    p = hexn(line + 2, i, 3);
    *p++ = ' ';
    for (j = i; j < i + 13 && j < n; j++) p = hexn(p, buf[j], 4);
    *p = 0;
    api->say(line);
  }
  p = put(line, "afpin ");
  *p++ = tag;
  p = hexn(put(p, " ticks="), ticks, 4);
  p = hexn(put(p, " way=1 hold="), s.hold, 1);
  p = hexn(put(p, " w="), s.w, 2);
  p = dbm(put(p, " rssi="), KM_RSSI_DBM(api->fd_read(0x67)));
  p = hexn(put(p, " sel="), (api->fd_read(0x47) >> 8) & 15u, 1);
  *p = 0;
  api->say(line);
  api->delay_us(50000); /* the serial port drains before the next capture */
}

/* The tone's captures: drive time, turns a sample. The drive time sets the share of "above"
   answers at rest (0.87, 0.68, 0.48 for 2, 3, 5 on the radio) and the size of the largest
   signal; a first-order converter makes tones of its own near simple fractions, 1/2 above all,
   so the tone is captured at several. */
#define N_TONE 6u

int k6_main(const k6_api_t *api) {
  const set_t rest = {3, 90}, sweep = {5, 90};
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(GPIOB + PA11_ODR) & AMP,
           m_in = moder & ~(3u << (2 * PA11_PIN)), room = 0, i, t0, t1 = 0, up = 0, v = 0;
  uint16_t r47 = api->fd_read(0x47);
  uint16_t *buf = (uint16_t *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  char line[64], *p;
  int ret = 0;
  if (room < 2 * N + 4) return -8;
  p = hexn(put(line, "afpin start 47="), r47, 4);
  p = hexn(put(p, " 48="), api->fd_read(0x48), 4);
  p = hexn(put(p, " moder="), moder, 8);
  p = hexn(put(p, " pupdr="), rd(PA11_GPIOA + PA11_PUPDR), 8);
  *p = 0;
  api->say(line);
  api->delay_us(50000);
  wr(GPIOB + G_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  for (i = 0; i < 18; i++) { /* m, h, then the sixteen selects */
    uint32_t sel = i < 2 ? i : i - 2, n = i < 2 ? N : N_SEL;
    if (api->key_poll()) {
      ret = -6;
      goto out;
    }
    api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (sel << 8)));
    t0 = capture(api, buf, n, m_in, i < 2 ? rest : sweep);
    dump(api, i < 2 ? "mh"[i] : (char)('A' + sel), buf, n, t0, i < 2 ? rest : sweep);
    ret++;
  }
  api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | 0x0100u));
  /* received audio with a signal: the bench's carrier, once it has stayed HOLD_MS */
  t0 = api->ms();
  for (;;) {
    v = api->fd_read(0x67);
    if (KM_RSSI_DBM(v) < RSSI_ON) {
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
      api->say("afpin no carrier");
      goto out;
    }
    api->delay_us(20000);
  }
  p = dbm(put(line, "afpin carrier rssi="), KM_RSSI_DBM(v));
  *p = 0;
  api->say(line);
  for (i = 0; i < N_TONE; i++) {
    set_t st = {5, 90};
    if (i == 1) st.hold = 3;
    if (i == 2) st.hold = 2;
    if (i == 3) st.hold = 8;
    if (i == 4) st.w = 180;
    if (api->key_poll()) {
      ret = -6;
      goto out;
    }
    api->delay_us(700000);
    t0 = capture(api, buf, N, m_in, st);
    dump(api, (char)('t' + i), buf, N, t0, st);
    ret++;
  }
out:
  api->fd_write(0x47, r47);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(GPIOB + PA11_BSRR, AMP);
  api->say("afpin done");
  return ret;
}
