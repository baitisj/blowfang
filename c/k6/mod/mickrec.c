/* mickrec: three seconds of the K6's own mic, keyed with the PA off, with the programming cable
   out (2026-11-21, docs/k6-mic-probe.md). micaf (run maf1a) found the mic on PA11 at AF selects
   1, 3, 4, 5, 9, 11-14 while the chip is keyed with the PA off and the mic ADC on, and nowhere in
   receive. This records select SEL there as micrec does (PA11 at 200 turns a sample, ~8 kHz, less
   a running mean, times 8, 4-bit IMA ADPCM, 3.2 s in 12.8 KB) for tools/k6_micprobe.py to decode.

   The screen runs the bench, as micout: PULL CABLE / THEN ANY KEY. Then the run is cued over the
   air, as micgain's passes are (pluto_bench cue): the K6 sends AG7EW in CW at full power on the
   bench frequency as its "ready" call and keys down; the job's Pluto hears it and sends the voice
   clip on SOUND_HZ (the IC-7100 there plays it at the mic); once that carrier has stayed HOLD_MS
   (past the Pluto's MCW ID) the K6 keys up again (tx_on: the core's guards) with the PA OFF, sets
   the mic ADC and the AF DAC (REG_30 bits 2, 9), select SEL, the AF gain and REG_7D's mic
   sensitivity MIC_SENS (micgain, mgn1b/mgn1d: 31 is the most, +15-17 dB over 0), settles PRE
   samples and records, GAIN times louder than mkr1a's (digital: the loudest of mkr1a was 1.6 %
   of full scale). Key-down; once the clip's carrier has gone, a last CW ID. Then PLUG CABLE IN /
   THEN ANY KEY, and only then the lines (micrec's own format, so tools/k6_micprobe.py reads them):
     mickrec start 30=bff1 47=6040 48=82df rx=1a46d650
     mickrec keyed 30=c1fa 36=007f now=c3fe 7d=e95f g=01
     p 0000 0123...
     micrec p ticks=7c38 n=6400 w=c8 rest=5d20 over=0000 g=01
     mickrec done
   The mic is never on the air: its bit is set only while REG_36 says the PA is off, checked
   every 512 samples. Writes, all put back: REG_30, REG_47, REG_48, REG_7D, the tuning, PA11's mode, speed
   and output bit, PB2. Returns the samples, or -9 (no sound), -8 (scratch under 1 KB), -7 (no
   key), -6 (a key mid-run), -5 (the PA would not go off, or REG_30 did not take the bits), -4
   (the bench frequency busy), -3 (tx_on refused), -2 (not on the bench frequency), -1 (no
   screen or transmitter). Level 13.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 13
#endif
#define K6_MODULE_OPT s
#include "../k6_mod.h"
#include "micadc.h"
#include "pa11adc.h"
#include "cableout.h"

#define AMP (1u << 2)
#ifndef SOUND_HZ
#define SOUND_HZ 445850000u
#endif
#ifndef BENCH_HZ
#define BENCH_HZ 440850000u
#endif
#ifndef PRE
#define PRE 1100u /* samples before the ones kept: the 1 uF coupling capacitor settles */
#endif
#ifndef MAX_BYTES
#define MAX_BYTES 12800u
#endif
#ifndef HOLD_MS
#define HOLD_MS 5000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 300000u
#endif
#ifndef KEY_WAIT_MS
#define KEY_WAIT_MS 1200000u
#endif
#ifndef SEL
#define SEL 4u
#endif
#ifndef MIC_SENS
#define MIC_SENS 31u /* REG_7D [4:0] while recording */
#endif
#ifndef GAIN
#define GAIN 1 /* digital, on top of x8 (mkr2a: 8 clipped 14 %, mkr2b: 3 with a 9 dB louder clip, 34 %) */
#endif
#ifndef DROP_MS
#define DROP_MS 1000u
#endif
#define RSSI_ON -90 /* dBm: the sound's carrier is sent weak */
#define HOLD 3u
#define W 200u
#define AF_DAC 0x0200u
#define CW_TEXT "AG7EW"
#define UNIT_US 60000u /* 20 wpm */
#define TX_MS 20000u

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

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }

/* Morse by code, as cwid.c: start at 1, then 0 for a dot and 1 for a dash (E = 2, A = 5) */
static int code_of(char c) {
  static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c) return i;
  return 0;
}

/* the PA on or off for n units; 0, -5 (the core keyed down) or -6 (a key) */
static int hold(const k6_api_t *api, int on, int n) {
  if (api->tx_pa(on) < 0) return -5;
  while (n--) {
    if (api->key_poll()) return -6;
    api->delay_us(UNIT_US);
  }
  return 0;
}

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

typedef struct {
  uint32_t ticks, n, rest, over, s30, now;
} rec_t;

/* the call: the CW ID at full power on the bench frequency, keyed down after. 0 or a negative error */
static int call(const k6_api_t *api) {
  const char *s;
  int e;
  if (rssi(api) >= RSSI_ON) return -4; /* never onto a busy channel */
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

/* keyed with the PA off: the recording into rec (n samples). 0 or a negative error; keyed down
   and REG_30, REG_7D put back either way */
static int record(const k6_api_t *api, uint32_t m_in, uint16_t r47, uint16_t r48, uint16_t r7d, uint8_t *rec,
                  rec_t *o) {
  pa11_t c = {0, 0, 0, 0};
  ima_t ima = {0, 0};
  int32_t mean = 0;
  uint32_t i, j, t0 = 0, want;
  int e = 0;
  if (rssi(api) >= RSSI_ON) return -4; /* never onto a busy channel */
  if (api->tx_on(TX_MS) != 0) return -3;
  if (api->tx_pa(0) < 0) e = -5;
  if (!e) {
    o->s30 = api->fd_read(0x30);
    if (api->fd_read(0x36) & 0x0080u) e = -5; /* the PA is still on: no mic bit */
  }
  want = (o->s30 | AF_DAC | MIC_ADC) & 0xFFFFu;
  if (!e && api->fd_read(0x30) != want && api->fd_write(0x30, (uint16_t)want) != 0) mic_bus_write(api, 0x30, want);
  if (!e && (o->now = api->fd_read(0x30)) != want) e = -5;
  if (!e) {
    api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (SEL << 8)));
    api->fd_write(0x48, (uint16_t)(r48 | 0x03F0u));
    api->fd_write(0x7D, (uint16_t)((r7d & ~0x001Fu) | MIC_SENS));
    api->delay_us(20000);
  }
  for (i = 0; i < PRE + o->n && !e; i++) {
    int32_t y = (int32_t)pa11_sample(m_in, HOLD, W, &c) << 5, x;
    uint32_t code;
    if (!(i & 511u) && (api->fd_read(0x36) & 0x0080u)) e = -5;
    if (i < 3) mean = y; /* (the first two samples are not yet differences) */
    mean += (y - mean) >> 5;
    if (i < PRE) continue;
    if (i == PRE) {
      t0 = api->ticks();
      o->rest = (uint32_t)mean >> 5;
    }
    x = ((y - mean) >> 2) * GAIN; /* 8 GAIN times the sample's distance from the running mean */
    if (x > 32767 || x < -32768) {
      x = x < 0 ? -32768 : 32767;
      o->over++;
    }
    code = ima_code(&ima, x);
    j = i - PRE;
    if (j & 1u) {
      rec[j >> 1] |= (uint8_t)(code << 4);
    } else {
      rec[j >> 1] = (uint8_t)code;
    }
  }
  o->ticks = (api->ticks() - t0) & 0xFFFFu;
  if (o->s30 && api->fd_read(0x30) != o->s30 && api->fd_write(0x30, (uint16_t)o->s30) != 0)
    mic_bus_write(api, 0x30, o->s30);
  api->fd_write(0x47, r47);
  api->fd_write(0x48, r48);
  api->fd_write(0x7D, r7d);
  api->tx_off();
  return e;
}

int k6_main(const k6_api_t *api) {
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz(),
           m_in = moder & ~(3u << (2 * PA11_PIN)), room = 0, t0, t1 = 0, up = 0, bytes, i, j;
  uint16_t r47 = api->fd_read(0x47), r48 = api->fd_read(0x48), r7d = api->fd_read(0x7D);
  int wide = api->fd_read(0x43) == 0x3028; /* (as gp0cap.c tells the filter) */
  char *line = (char *)(((uint32_t)api->scratch(&room) + 3) & ~3u), *p;
  uint8_t *rec = (uint8_t *)line + 64;
  rec_t o;
  int ret = 0, e = 0;
  if (api->level < 6) return -1;
  o.ticks = o.n = o.rest = o.over = o.s30 = o.now = 0; /* (no memset: no runtime library) */
  if (room < 1024) return -8;
  bytes = room - 68;
  if (bytes > MAX_BYTES) bytes = MAX_BYTES;
  bytes -= bytes % 26u;
  o.n = 2 * bytes;
  p = hexn(put(line, "mickrec start 30="), api->fd_read(0x30), 4);
  p = hexn(put(p, " 47="), r47, 4);
  p = hexn(put(p, " 48="), r48, 4);
  p = hexn(put(p, " rx="), home, 8);
  *p = 0;
  api->say(line);
  if (home != BENCH_HZ) return -2;
  wr(MIC_GPIOB + MIC_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  co_show(api, "MICKREC CABLE OUT", "PULL CABLE", "THEN ANY KEY");
  if (co_key(api, KEY_WAIT_MS) < 0) {
    ret = -7;
    goto out;
  }
  co_show(api, "MICKREC CABLE OUT", "QUIET PLEASE", "CALLING");
  /* a quiet second on the bench frequency, then the call */
  for (t0 = t1 = api->ms(); api->ms() - t0 < 30000u && api->ms() - t1 < 1000u;) {
    if (rssi(api) >= RSSI_ON) t1 = api->ms();
    api->delay_us(20000);
  }
  e = call(api);
  if (e) {
    ret = e;
    goto out;
  }
  co_show(api, "MICKREC CABLE OUT", "QUIET PLEASE", "WAITING");
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
    if (api->key_poll()) e = -6;
    else if (api->ms() - t0 >= WAIT_MS) e = -9;
    if (e) break;
    api->delay_us(20000);
  }
  api->tune(home, wide);
  api->delay_us(300000);
  if (!e) {
    co_show(api, "MICKREC CABLE OUT", "QUIET PLEASE", "RECORDING");
    e = record(api, m_in, r47, r48, r7d, rec, &o);
  }
  if (!e) { /* the clip's carrier gone DROP_MS, then the last ID */
    api->tune(SOUND_HZ, wide);
    for (t0 = t1 = api->ms(); api->ms() - t0 < WAIT_MS && api->ms() - t1 < DROP_MS;) {
      if (rssi(api) >= RSSI_ON) t1 = api->ms();
      api->delay_us(20000);
    }
    api->tune(home, wide);
    api->delay_us(300000);
    call(api);
  }
  ret = e ? e : (int)o.n;
out:
  api->tx_off();
  api->tune(home, wide); /* (REG_30 whole again: the core's receive word) */
  api->fd_write(0x47, r47);
  api->fd_write(0x48, r48);
  api->fd_write(0x7D, r7d);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  if (ret != -7) {
    co_show(api, "MICKREC CABLE OUT", "PLUG CABLE IN", "THEN ANY KEY");
    co_key(api, KEY_WAIT_MS);
    co_show(api, "MICKREC CABLE OUT", "SENDING", "");
    api->delay_us(1000000);
    p = hexn(put(line, "mickrec keyed 30="), o.s30, 4);
    p = hexn(put(p, " 36="), api->fd_read(0x36), 4);
    p = hexn(put(p, " now="), o.now, 4);
    p = hexn(put(p, " 7d="), (r7d & ~0x001Fu) | MIC_SENS, 4);
    p = hexn(put(p, " g="), GAIN, 2);
    *p = 0;
    api->say(line);
  }
  if (ret > 0) {
    for (i = 0; i < bytes; i += 26) {
      p = hexn(put(line, "p "), i, 4);
      *p++ = ' ';
      for (j = i; j < i + 26; j++) p = hexn(p, rec[j], 2);
      *p = 0;
      api->say(line);
      api->delay_us(15000); /* the serial port drains */
    }
    p = hexn(put(line, "micrec p ticks="), o.ticks, 4);
    p = hexn(put(p, " n="), o.n, 4);
    p = hexn(put(p, " w="), W, 2);
    p = hexn(put(p, " rest="), o.rest, 4);
    p = hexn(put(p, " over="), o.over, 4);
    p = hexn(put(p, " g="), GAIN, 2);
    *p = 0;
    api->say(line);
  }
  api->say("mickrec done");
  return ret;
}
