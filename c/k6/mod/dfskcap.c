/* dfskcap: what does the FD6818's plain FSK receiver (REG_58 RX mode 0) make of Bell 202 audio?

   The chip's 1200/2400 Hz receiver reads flat Bell 202 but no packet with a sender's
   pre-emphasis and none with noise (docs/k6-aprs-clock.md, the fourth bench run). Its plain FSK
   receiver slices the audio itself: probe 2's one capture at 4800 baud (REG_58 = 00E9), read
   again, shows 2200 Hz as alternating bits (a transition every sample) and 1200 Hz as 0011 (one
   every second sample), on a time base within 1% of 4800 a second. Runs of two and more mark
   bits read clean; a lone mark bit comes as one missing transition or two, and a first decoder
   gets 85% of the bits. A slicer of the audio's sign does not care which tone is the louder,
   which is what the 1200/2400 receiver trips on. But a packet of the chip's is 256 bytes, 0.43 s
   at 4800 baud, and an APRS packet is a second.

   This module records, for the host to read (python3 -m modem.aprs dfsklog LOG): words of that
   receiver on the bench's rehearsal of real packets (python3 -m modem.aprs wav OUT.wav --now
   --kinds --repeat N). It asks:
     - does REG_5D take a length over 256 bytes (its bits 7:5, as on a BK4819)? dn= says: 427 ms
       for 256 bytes at 4800 baud, 3413 ms for 2048.
     - which sync word starts it on a plain packet: AAAA (16 samples of 2200 Hz, in an opening of
       flags whose long tone is the space) or 6666 (of 1200 Hz)?
     - what do the words look like for a pre-emphasised, a weak and a noisy packet?
     - is 6000 baud (five samples a bit) a better sampler than 4800, and does the receiver start
       there at all (sync words: E739 = 1200 Hz, D2DA = 2200 Hz as a true sampler would read
       them)?
   The settings, one after the other from WAIT_MS (32 s) after the start, each for its time:
      0  00e9  4800 baud  sync aaaa  REG_5D ffe0   45 s   the main one
      1  00e9  4800       aaaa       ff00           8 s   probe 2's length
      2  00e9  4800       6666       ffe0          15 s
      3  03e9  4800       aaaa       ffe0          15 s   RX gain bits
      4  00e9  6000       e739       ffe0          20 s
      5  00e9  6000       d2da       ffe0          20 s
      6  00e9  4800       aaaa       ffe0          15 s   REG_2B + 0100 (de-emphasis off)
   The FIFO is read by time, a word every 16 bits of the setting's baud, three words behind the
   chip (REG_5E: almost full at 6, and then a word more is read). A word unlike the one before
   starts a capture (turning the modem on raises the sync flag by itself, and an empty FIFO
   gives its last word again); it ends 8 words after RX-done, at CAPW words or after 5 s. Then
   the modem is turned off and on for the next. Lines:
     dfskcap start hz=440850000 n=7
     s 0 t=1234 cfg=00e9 b=c195 sy=aaaa l=ffe0      a setting begins
     c0 3 t=12345 dn=3413 w=1032 wd=1024 i=3002 f=2 r=-21
                                        setting, capture, ms at its first word, ms from there
                                        to RX-done (0: none), words read, words read at RX-done,
                                        REG_02's flags seen, extra reads for almost-full, RSSI
     d0 3 000 aaaaaaaa5555...           its words, 12 a line, as on the air (first bit first);
                                        the offset counts words, in hex
     v0 n=9 w=9288                      at the setting's end: captures, words
     dfskcap end n=31
   Only the first CAPN captures of a setting are said. Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E,
   72, 2B, 3F and 02, put back as read. Receive only. Returns the captures, or -3 (old image),
   -8 (scratch). Any key ends it.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT z
#include "../k6_mod.h"

#ifndef PROBE_HZ
#define PROBE_HZ 440850000u
#endif
#ifndef SLOT_SCALE
#define SLOT_SCALE 1000u /* ms a unit of a setting's time (the emulated test shortens it) */
#endif
#ifndef WAIT_MS
#define WAIT_MS 32000u   /* before the first setting: the module starts about 29 s before its own
                            upload's audio ends, and that is no Bell 202 */
#endif
#define NV 7
#define CAPW 1040u       /* words kept of a capture: 2048 bytes and the 8 after RX-done, and a few */
#define CAPN 12u         /* captures said per setting */
#define LAG 3u           /* words the timed reads stay behind the chip (its FIFO holds 8) */
#define TAIL 8u          /* words read after RX-done: what the FIFO still held */
#define CAP_MS 5000u     /* a capture's longest */

#define IRQ_DONE 0x2000u
#define IRQ_FULL 0x1000u
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u
#define CTRL 0x00F0u     /* a sync word of two bytes */

typedef struct {
  uint16_t cfg, baud, sync, len, af;
  uint16_t wticks3; /* three word times in 100 us ticks: 100 at 4800 baud, 80 at 6000 */
  uint16_t secs;
} var_t;

static const var_t V[NV] = {
    {0x00E9, 0xC195, 0xAAAA, 0xFFE0, 0, 100, 45},
    {0x00E9, 0xC195, 0xAAAA, 0xFF00, 0, 100, 8},
    {0x00E9, 0xC195, 0x6666, 0xFFE0, 0, 100, 15},
    {0x03E9, 0xC195, 0xAAAA, 0xFFE0, 0, 100, 15},
    {0x00E9, 0xF1FB, 0xE739, 0xFFE0, 0, 80, 20},
    {0x00E9, 0xF1FB, 0xD2DA, 0xFFE0, 0, 80, 20},
    {0x00E9, 0xC195, 0xAAAA, 0xFFE0, 0x0100, 100, 15},
};

static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F};
#define SAVED_2B 8

typedef struct {
  uint16_t cap[CAPW];
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

static char *hexn(char *p, uint32_t v, int digits) {
  int i;
  for (i = 4 * (digits - 1); i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v); }
static char *kx(char *p, const char *k, uint32_t v) { return hexn(put(p, k), v, 4); }

static void arm(const k6_api_t *api) {
  api->fd_write(0x59, CTRL | CTRL_CLR);
  api->fd_write(0x59, CTRL | CTRL_EN);
  api->fd_write(0x02, 0);
}

/* the modem off and on with this setting, armed twice (a new sync word alone does not take
   hold, and arming alone does not end a packet the chip is in); -> the FIFO's stale last word */
static uint32_t seek(const k6_api_t *api, const var_t *v, uint32_t af) {
  uint32_t i, w = 0;
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x2B, af | v->af);
  api->fd_write(0x5A, v->sync);
  api->fd_write(0x5B, v->sync);
  api->fd_write(0x58, v->cfg);
  api->fd_write(0x72, v->baud);
  api->fd_write(0x5C, 0x5625); /* no CRC of the chip's */
  api->fd_write(0x5D, v->len);
  api->fd_write(0x5E, 0x0206); /* RX almost full at 6 words */
  arm(api);
  api->delay_us(2000);
  arm(api);
  for (i = 0; i < 8; i++) w = api->fd_read(0x5F);
  return (w & 0xFF) << 8 | w >> 8;
}

/* a setting begins: its s line, the modem set; -> the FIFO's stale last word */
static uint32_t begin(const k6_api_t *api, int cur, uint32_t af) {
  const var_t *v = &V[cur];
  char ln[64], *p = ln;
  *p++ = 's', *p++ = ' ', p = dec(p, (uint32_t)cur);
  p = kv(p, " t=", api->ms());
  p = kx(p, " cfg=", v->cfg);
  p = kx(p, " b=", v->baud);
  p = kx(p, " sy=", v->sync);
  p = kx(p, " l=", v->len);
  *p = 0;
  api->say(ln);
  return seek(api, v, af);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now = 0, tkey = 0, t0, tcap = 0, dn = 0, prev, total = 0, af;
  uint32_t got = 0, wd = 0, tail = 0, irq = 0, nfull = 0, ncap = 0, nw = 0, rssi = 0, tlast;
  int32_t acc = 0;
  uint16_t saved[sizeof(SAVED)];
  int cur = 0, in = 0, key = 0;
  char ln[64], *p;
  const var_t *v = &V[0];
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(SAVED); i++) saved[i] = api->fd_read(SAVED[i]);
  af = saved[SAVED_2B];
  api->tune(PROBE_HZ, 1);
  p = kv(ln, "dfskcap start hz=", PROBE_HZ);
  p = kv(p, " n=", NV);
  *p = 0;
  api->say(ln);
  while (api->ms() < WAIT_MS && !key) {
    api->delay_us(50000);
    key = api->level >= 4 ? api->key_poll() : api->key_down();
  }
  prev = begin(api, cur, af);
  t0 = api->ms(), tlast = api->ticks() & 0xFFFF;
  for (;;) {
    uint32_t f, tk, n = 0, over = 0;
    now = api->ms();
    if (key) break;
    if (now - tkey >= 50) {
      tkey = now;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key) break;
    }
    if (!in && now - t0 >= v->secs * SLOT_SCALE) {
      p = ln, *p++ = 'v', p = dec(p, (uint32_t)cur);
      p = kv(p, " n=", ncap);
      p = kv(p, " w=", nw);
      *p = 0;
      api->say(ln);
      total += ncap, ncap = nw = 0;
      if (++cur == NV) break;
      v = &V[cur];
      prev = begin(api, cur, af);
      t0 = api->ms(), acc = 0, tlast = api->ticks() & 0xFFFF;
      continue;
    }
    f = api->fd_read(0x02) & (IRQ_DONE | IRQ_FULL | 2u);
    if (f) api->fd_write(0x02, 0);
    tk = api->ticks() & 0xFFFF;
    acc += (int32_t)(((tk - tlast) & 0xFFFF) * 3), tlast = tk;
    while (acc >= (int32_t)v->wticks3 && n < 8) acc -= (int32_t)v->wticks3, n++;
    if (in) {
      irq |= f;
      if ((f & IRQ_FULL) && n < 8) n++, nfull++;
      if ((f & IRQ_DONE) && !dn) dn = now - tcap + !(now - tcap), wd = got;
    }
    while (n && !over) {
      uint32_t w = api->fd_read(0x5F), x = (w & 0xFF) << 8 | w >> 8;
      n--;
      if (!in) {
        if (x == prev) continue;
        /* the first word of a packet: from here the reads stay LAG words behind the chip */
        in = 1, got = 0, dn = 0, wd = 0, tail = 0, irq = f, nfull = 0, tcap = now;
        rssi = api->fd_read(0x67) & 0x1FF;
        acc = -(int32_t)(LAG * v->wticks3), n = 0;
      }
      if (got < CAPW) s->cap[got] = (uint16_t)x;
      got++;
      if (dn && ++tail >= TAIL) over = 1;
      if (got >= CAPW) over = 1;
    }
    if (in && now - tcap >= CAP_MS) over = 1;
    if (in && over) {
      ncap++, nw += got;
      if (ncap <= CAPN) {
        p = ln, *p++ = 'c', p = dec(p, (uint32_t)cur), *p++ = ' ', p = dec(p, ncap);
        p = kv(p, " t=", tcap);
        p = kv(p, " dn=", dn);
        p = kv(p, " w=", got);
        p = kv(p, " wd=", wd);
        p = kx(p, " i=", irq);
        p = kv(p, " f=", nfull);
        p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(rssi));
        *p = 0;
        api->say(ln);
        for (i = 0; i < got; i++) {
          uint32_t col = i - 12 * udiv(i, 12);
          if (!col) {
            p = ln, *p++ = 'd', p = dec(p, (uint32_t)cur), *p++ = ' ', p = dec(p, ncap);
            *p++ = ' ', p = hexn(p, i, 3), *p++ = ' ';
          }
          p = hexn(p, s->cap[i], 4);
          if (col == 11 || i + 1 == got) {
            *p = 0;
            api->say(ln);
          }
        }
      }
      prev = seek(api, v, af);
      in = 0, acc = 0, tlast = api->ticks() & 0xFFFF;
    }
    api->delay_us(300);
  }
  for (i = 0; i < sizeof(SAVED); i++) api->fd_write(SAVED[i], saved[i]);
  api->fd_write(0x02, 0);
  if (cur < NV) total += ncap; /* a key, mid-setting */
  p = kv(ln, "dfskcap end n=", total);
  *p = 0;
  api->say(ln);
  return (int)total;
}
