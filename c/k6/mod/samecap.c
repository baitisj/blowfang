/* samecap: does the FD6818 read NOAA SAME (the weather radio's alert headers)? A probe.

   SAME (47 CFR 11.31) is AFSK on the FM audio: mark 2083.3 Hz (a 1), space 1562.5 Hz (a 0), 520.83
   baud, each byte its low bit first; a header is sixteen bytes of AB, then "ZCZC-..." text, sent
   three times a second apart. The chip's datasheet lists a "NOAA SAME RX" mode: the plain FSK
   receiver (REG_58 RX mode 0) with its RX bandwidth bits 3:1 at 2 (00E5) and REG_72 about 5377
   (520.8 baud). The plain receiver slices the FM discriminator's sign, which for two audio tones
   means nothing, unless that bandwidth setting puts a tone discriminator in front of it. This
   module records what it gives, for the host to read (python3 -m modem.same report LOG).

   The preamble AB, low bit first, is D5 on the air top bit first: the chip's sync word is D5D5
   (or 2A2A should the chip's sense of mark and space be the other one). Settings, FE_S each after
   WAIT_MS:
      0  00e5  sync d5d5     the datasheet's SAME mode
      1  00e5  sync 2a2a     the tones the other way round
      2  00e9  sync d5d5     the plain receiver as the APRS sampler had it
      3  00e9  sync 2a2a
   A capture starts at the chip's first word (unlike what the empty FIFO gave before) and takes
   CAPW words by time, two behind the chip; its words are said 12 a line:
     samecap start hz=440850000 n=4
     s 1 t=32000 cfg=00e5 sy=2a2a
     c 3 s=1 t=40123 r=-45              capture, setting, ms at its first word, RSSI
     d 3 00 d5d5d5d5...                 its words as on the air, offset in words (hex)
     v 1 n=4                            a setting's end, captures
     samecap end n=12 z=0               (z: captures not like a preamble, SAME_PRE builds)
   Only CAPN captures a setting are said. Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72 and 3F, put
   back as read. Receive only. Returns the captures, or -3 (old image), -8 (scratch).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#ifndef K6_MODULE_OPT
#define K6_MODULE_OPT z
#endif
#include "../k6_mod.h"

#ifndef SAME_HZ
#define SAME_HZ 440850000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 32000u
#endif
#ifndef FE_S
#define FE_S 40u
#endif
#ifndef SLOT_SCALE
#define SLOT_SCALE 1000u
#endif
#ifndef NV
#define NV 4
#endif
#define B72 5377u        /* 520.83 baud x 10.32444 */
#define WT 4915u         /* a word in 1/16 ticks of 100 us: 16 / 520.83 s */
#define LAG 2u
#define CAPW 64u         /* words a capture: 128 bytes, 1.97 s (a header is about 80 bytes) */
#ifndef CAPN
#define CAPN 8u
#endif
#define CTRL 0x00F0u
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u

static const uint16_t CFG[4] = {0x00E5, 0x00E5, 0x00E9, 0x00E9};
static const uint16_t SY[4] = {0xD5D5, 0x2A2A, 0xD5D5, 0x2A2A};
static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x3F};
#define NSV sizeof(SAVED)

typedef struct {
  uint16_t sv[NSV];
  uint16_t cap[CAPW];
  uint32_t el;
  uint16_t tl;
  char ln[64];
} st_t;

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static uint32_t udiv(uint32_t n, uint32_t d) {
  uint32_t q = 0, bit = 1;
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

static char *hexn(char *p, uint32_t v, int digits) {
  int i;
  for (i = 4 * (digits - 1); i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v); }

static void say(const k6_api_t *api, st_t *s, char *end) {
  *end = 0;
  api->say(s->ln);
}

#ifdef SAME_PRE
static uint32_t pop(uint32_t v) {
  uint32_t n = 0;
  while (v) v &= v - 1, n++;
  return n;
}

/* does the capture go on like a preamble (two of its first three words within 3 bits of D5D5 or
   2A2A)? SAME_PRE builds say only those (noise gives the sync word every couple of minutes) */
static int pre_like(const uint16_t *w) {
  int i, k = 0;
  for (i = 0; i < 3; i++) k += pop(w[i] ^ 0xD5D5u) <= 3 || pop(w[i] ^ 0x2A2Au) <= 3;
  return k >= 2;
}
#endif

static uint32_t swap(uint32_t w) { return (w & 0xFF) << 8 | w >> 8; }

static uint32_t clk(const k6_api_t *api, st_t *s) {
  uint16_t t = (uint16_t)api->ticks();
  s->el += (uint16_t)(t - s->tl);
  s->tl = t;
  return s->el;
}

/* the modem off and on with setting c, armed twice; -> what the empty FIFO gives */
static uint32_t seek(const k6_api_t *api, int c) {
  uint32_t i, w = 0;
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x5A, SY[c]);
  api->fd_write(0x5B, SY[c]);
  api->fd_write(0x58, CFG[c]);
  api->fd_write(0x72, B72);
  api->fd_write(0x5C, 0x5625); /* no CRC of the chip's */
  api->fd_write(0x5D, 0x7F00); /* 128 bytes */
  api->fd_write(0x5E, 0x0206);
  for (i = 0; i < 2; i++) {
    api->fd_write(0x59, CTRL | CTRL_CLR);
    api->fd_write(0x59, CTRL | CTRL_EN);
    api->fd_write(0x02, 0);
    if (!i) api->delay_us(2000);
  }
  for (i = 0; i < 8; i++) w = api->fd_read(0x5F);
  return swap(w);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, k, nz = 0, now, tkey = 0, tset, prev, t0 = 0, n = 0, ncap = 0, total = 0, rpk = 0, tcap = 0;
  int key = 0, cur = 0, cap = 0;
  char *p;
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < NSV; i++) s->sv[i] = api->fd_read(SAVED[i]);
  s->el = 0, s->tl = (uint16_t)api->ticks();
  api->tune(SAME_HZ, 1);
  p = kv(s->ln, "samecap start hz=", SAME_HZ);
  p = kv(p, " n=", NV);
  say(api, s, p);
  while (api->ms() < WAIT_MS && !key) {
    api->delay_us(50000);
    key = api->level >= 4 ? api->key_poll() : api->key_down();
  }
  tset = api->ms();
  p = kv(s->ln, "s ", 0);
  p = kv(p, " t=", tset);
  p = hexn(put(p, " cfg="), CFG[0], 4);
  p = hexn(put(p, " sy="), SY[0], 4);
  say(api, s, p);
  prev = seek(api, 0);
  while (!key) {
    now = api->ms();
    if (now - tkey >= 50) {
      uint32_t r = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      if (r > rpk) rpk = r;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key) break;
    }
    if (!cap && now - tset >= FE_S * SLOT_SCALE) {
      p = kv(s->ln, "v ", (uint32_t)cur);
      p = kv(p, " n=", ncap);
      say(api, s, p);
      total += ncap, ncap = 0;
      if (++cur >= NV) break;
      tset = now;
      p = kv(s->ln, "s ", (uint32_t)cur);
      p = kv(p, " t=", now);
      p = hexn(put(p, " cfg="), CFG[cur], 4);
      p = hexn(put(p, " sy="), SY[cur], 4);
      say(api, s, p);
      prev = seek(api, cur);
    }
    if (!cap) {
      uint32_t v = swap(api->fd_read(0x5F));
      if (v != prev) {
        cap = 1, n = 0, t0 = clk(api, s) << 4, tcap = now, rpk = 0;
        s->cap[n++] = (uint16_t)v;
      }
    }
    while (cap && (int32_t)((clk(api, s) << 4) - t0 - (n + LAG) * WT) >= 0) {
      s->cap[n++] = (uint16_t)swap(api->fd_read(0x5F));
      if (n < CAPW) continue;
      cap = 0;
#ifdef SAME_PRE
      if (!pre_like(s->cap)) {
        nz++;
        prev = seek(api, cur);
        continue;
      }
#endif
      ncap++;
      if (ncap <= CAPN) {
        p = kv(s->ln, "c ", total + ncap);
        p = kv(p, " s=", (uint32_t)cur);
        p = kv(p, " t=", tcap);
        p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(rpk));
        say(api, s, p);
        for (i = 0, k = 0; i < CAPW; i++, k = k == 11 ? 0 : k + 1) {
          if (!k) {
            p = kv(s->ln, "d ", total + ncap);
            *p++ = ' ', p = hexn(p, i, 2), *p++ = ' ';
          }
          p = hexn(p, s->cap[i], 4);
          if (k == 11 || i + 1 == CAPW) say(api, s, p);
        }
      }
      prev = seek(api, cur);
    }
    api->delay_us(300);
  }
  for (i = 0; i < NSV; i++) api->fd_write(SAVED[i], s->sv[i]);
  api->fd_write(0x02, 0);
  total += ncap;
  p = kv(s->ln, "samecap end n=", total);
  p = kv(p, " z=", nz);
  say(api, s, p);
  return (int)total;
}
