/* micrec: three seconds of AF select 4 with the mic ADC and TX DSP on, recorded by the MCU
   (2026-11-20, docs/k6-mic-probe.md). Written as a recording "from the K6's own mic"; what that
   select carries in this state is the receiver's raw baseband, and the voice it recorded was
   the bench's Pluto leaking in by radio (the write-up's "The correction"). Kept as the recorder
   it is: the FD6818's mic ADC and TX DSP on while the K6 receives (micadc.h; the PA off, nothing
   keyed), REG_47's AF output select 4, PA11 as the converter (pa11adc.h) at 200 turns a sample,
   about 8 kHz.

   A sample (0 .. 40000) loses its slow part (y - a running mean over 32 samples' worth), is
   scaled by 8 and coded as 4-bit IMA ADPCM into the scratch: as many samples as it holds, two
   a byte (3.2 s in 12.8 KB). The recording starts once a carrier has stayed 5 s on 445.850 MHz
   (another radio there plays the bench's voice clip at the K6's mic), with the K6 back on its
   own channel; -DNO_WAIT records at once (a voice at the mic, no bench).

   Lines (under 60 characters; tools/k6_micprobe.py decodes them and writes the WAV):
     micrec start 30=bff1 47=6040 48=82df 7d=e95c
     micrec sound rssi=-3d            (or: micrec no sound)
     p 0000 0123...                   the recording, 26 bytes a line from that byte (hex);
                                      a byte's low four bits are the earlier sample
     micrec p ticks=7c38 n=6400 w=c8 rest=5d20 over=0000
                                      the recording's length in 100 us ticks, its samples, the
                                      converter's turns a sample (drive time 3), the first
                                      sample's running mean and the samples that clipped (hex)
     micrec check rssi=-3d            445.850 after the recording: the sound was still there
     micrec done
   Writes, all put back: REG_30 (bits 2, 1, by micadc.h's rule), REG_47 (the select), REG_48
   (AF gain 2 at its most), the tuning, PA11's mode, speed and output bit, PB2. Returns the
   samples recorded, or -8 (scratch under 1 KB), -6 (a key), -5 (REG_30 would not take the
   bits, or the chip is not in receive with the PA off). Level 4.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 4
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "micadc.h"
#include "pa11adc.h"

#define AMP (1u << 2)
#ifndef SOUND_HZ
#define SOUND_HZ 445850000u
#endif
#ifndef PRE
#define PRE 1100u /* samples before the ones kept: the 1 uF coupling capacitor settles */
#endif
#ifndef MAX_BYTES
#define MAX_BYTES 12800u
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
#define RSSI_ON -85 /* dBm */
#define HOLD 3u
#define W 200u
#define SEL 4u

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

#ifndef NO_WAIT
static char *dbm(char *p, int v) {
  if (v < 0) {
    *p++ = '-';
    v = -v;
  }
  return hexn(p, (uint32_t)v, 2);
}

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }
#endif

/* IMA ADPCM, the standard coder: 4 bits a sample */
typedef struct {
  int32_t pred, index;
} ima_t;

static uint32_t ima_step(int32_t index) {
  static const uint16_t STEPS[89] = {
      7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,   28,
      31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,  118,
      130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,  494,
      544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878, 2066,
      2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845, 8630,
      9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
  return STEPS[index];
}

static uint32_t ima_code(ima_t *s, int32_t x) {
  int32_t step = (int32_t)ima_step(s->index), d = x - s->pred, q = step >> 3;
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

int k6_main(const k6_api_t *api) {
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           m_in = moder & ~(3u << (2 * PA11_PIN)), room = 0, t0, t1, up = 0, bytes, n, i, j, over = 0, rest = 0;
  uint16_t r47 = api->fd_read(0x47), r48 = api->fd_read(0x48);
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  char *line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u), *p;
  uint8_t *rec = (uint8_t *)line + 64;
  pa11_t c = {0, 0, 0, 0};
  ima_t ima = {0, 0};
  int32_t mean = 0;
  int ret = 0;
  if (room < 1024) return -8;
  bytes = room - 68;
  if (bytes > MAX_BYTES) bytes = MAX_BYTES;
  bytes -= bytes % 26u;
  n = 2 * bytes;
  p = hexn(put(line, "micrec start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 47="), r47, 4);
  p = hexn(put(p, " 48="), r48, 4);
  p = hexn(put(p, " 7d="), api->fd_read(0x7D), 4);
  *p = 0;
  api->say(line);
  wr(MIC_GPIOB + MIC_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
#ifndef NO_WAIT
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
  /* the sound: a carrier on SOUND_HZ that stays HOLD_MS (its ID over, the voice on) */
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
      api->say("micrec no sound");
      goto out;
    }
    api->delay_us(20000);
  }
  *dbm(put(line, "micrec sound rssi="), rssi(api)) = 0;
  api->say(line);
  api->tune(home, wide);
  api->delay_us(50000);
#else
  (void)t1;
  (void)up;
#endif
  if (mic_state(api, MIC_BITS) < 0) {
    ret = -5;
    goto out;
  }
  api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (SEL << 8)));
  api->fd_write(0x48, (uint16_t)(r48 | 0x03F0u));
  api->delay_us(30000); /* the line above has left the serial port */
  t0 = 0;
  for (i = 0; i < PRE + n; i++) {
    int32_t y = (int32_t)pa11_sample(m_in, HOLD, W, &c) << 5, x;
    uint32_t code;
    if (i < 3) mean = y; /* (the first two samples are not yet differences) */
    mean += (y - mean) >> 5;
    if (i < PRE) continue;
    if (i == PRE) {
      t0 = api->ticks();
      rest = (uint32_t)mean >> 5;
    }
    x = (y - mean) >> 2; /* 8 times the sample's distance from the running mean */
    if (x > 32767 || x < -32768) {
      x = x < 0 ? -32768 : 32767;
      over++;
    }
    code = ima_code(&ima, x);
    j = i - PRE;
    if (j & 1u) {
      rec[j >> 1] |= (uint8_t)(code << 4);
    } else {
      rec[j >> 1] = (uint8_t)code;
    }
  }
  t0 = (api->ticks() - t0) & 0xFFFFu;
  mic_state(api, 0);
  for (i = 0; i < bytes; i += 26) {
    p = hexn(put(line, "p "), i, 4);
    *p++ = ' ';
    for (j = i; j < i + 26; j++) p = hexn(p, rec[j], 2);
    *p = 0;
    api->say(line);
  }
  p = hexn(put(line, "micrec p ticks="), t0, 4);
  p = hexn(put(p, " n="), n, 4);
  p = hexn(put(p, " w="), W, 2);
  p = hexn(put(p, " rest="), rest, 4);
  p = hexn(put(p, " over="), over, 4);
  *p = 0;
  api->say(line);
  ret = (int)n;
#ifndef NO_WAIT
  api->tune(SOUND_HZ, wide);
  api->delay_us(300000);
  *dbm(put(line, "micrec check rssi="), rssi(api)) = 0;
  api->say(line);
#endif
out:
  mic_state(api, 0);
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r47);
  api->fd_write(0x48, r48);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  api->say("micrec done");
  return ret;
}
