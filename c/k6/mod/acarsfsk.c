/* acarsfsk: can the FD6818's own FSK modem read ACARS (docs/k6-acars.md)?

   ACARS is MSK at 2400 bit/s on 1200 and 2400 Hz, the native format of the chip's 1200/2400 Hz
   receiver (REG_58<12:10> = 4), which reads Bell 202 for aprsfsk. Two things are not known:
   whether that receiver starts on an ACARS block (no alternating preamble: 2400 Hz, then "+*"
   and SYN SYN), and whether the modem can be fed from an AM signal at all (ACARS is AM; the
   modem sits behind the FM demodulator). This probe asks both on the bench channel, 440.850
   MHz, receive only. The test signal (python3 -m modem.acars probe OUT.wav) is K and R units in
   turn: R a real ACARS block, K the same block after the chip's own kind of opening
   (alternating tones and the pager's sync word). The IC-7100 sends it in FM for the control
   build (acarsfsk: is the framing readable?) and in AM for acarsfsk_am.

   A setting is a front end and a modem setting, SLOT_MS each by the module's clock, round and
   round until RUN_MS (any key ends it):
     front ends (acarsfsk has only the first):
       0  as found: FM, wide, on the frequency
       1  REG_47's AF source = AM (7), wide          does the modem follow the AF source?
       2  the same, narrow
       3  FM, narrow, tuned 2.5 kHz high             an AM signal with one sideband cut by the
       4  FM, narrow, tuned 2.5 kHz low              filter is partly FM: slope detection
       5  FM, narrow, tuned 4 kHz high
     modem: REG_58 = 33e9, 33a9, 33a3 (and 33e3 in acarsfsk), each with the sync word
       076B 3E36 (K units), 5C5C in two bytes (SYN SYN as tones: R units) and, in acarsfsk,
       3FFE 5C5C ("+*" SYN SYN)
   REG_72 = 2400 baud, 80 bytes a packet, read by time two words behind the chip.
     acarsfsk start hz=440850000 slot=5000 n=12
     c03 2 t=17012 w=40 st=64        setting, its capture, ms at the sync interrupt, words, REG_0B
     d03 2 000 0180 ...              the words, 10 a line (the first CAPN captures of a setting)
     v03 f=0 33e9 5c5c sy=4 soh=4 ok=4 r=-21    at the slot's end: syncs, blocks opening with SOH
                                     and blocks with a good check (either sense of the tones),
                                     the strongest RSSI
     acarsfsk end ok=31
   Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 47, 3F and 02, put back as read, and retunes (the
   pager tunes itself again after any module). Returns the blocks with a good check, or -2 (a
   key before any), -3 (old image), -8 (scratch).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT z
#include "../k6_mod.h"

#ifndef PROBE_HZ
#define PROBE_HZ 440850000u
#endif
#ifndef SLOT_MS
#define SLOT_MS 5000u
#endif
#ifdef ACARS_AM
#define NFE 6
#define NCFG 3
#define NSYNC 2
#ifndef RUN_MS
#define RUN_MS 181000u
#endif
#else
#define NFE 1
#define NCFG 4
#define NSYNC 3
#ifndef RUN_MS
#define RUN_MS 121000u
#endif
#endif
#define NV (NFE * NCFG * NSYNC)
#define WORDS 40 /* 80 bytes: the test block is 62 */
#define CAPN 3
#define LAG 2

static const uint16_t CFG[4] = {0x33E9, 0x33A9, 0x33A3, 0x33E3};
/* REG_5A, REG_5B, REG_59 */
static const uint16_t SYNC[3][3] = {{0x076B, 0x3E36, 0xF8}, {0x5C5C, 0x5C5C, 0xF0}, {0x3FFE, 0x5C5C, 0xF8}};
/* tuning offset in 100 Hz, wide, AF source (0: as found) */
static const int8_t FE[6][3] = {{0, 1, 0}, {0, 1, 7}, {0, 0, 7}, {25, 0, 0}, {-25, 0, 0}, {40, 0, 0}};
static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x47, 0x3F};
#define SAVED_47 8

typedef struct {
  uint32_t sy, soh, ok;      /* the setting now */
  uint32_t crc[2], nb[2];    /* a block's check and bit count, for either sense of the tones */
  uint16_t rmax, saved[sizeof(SAVED)], cap[WORDS];
  uint8_t d[2], byte[2], st[2]; /* the last data bit, the byte being built, 0 open 1 body 2..3 check 4 over */
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

static char *hex4(char *p, uint32_t v) {
  int i;
  for (i = 12; i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v); }

static char *tag(char *p, char c, int n) {
  *p++ = c, *p++ = (char)('0' + udiv((uint32_t)n, 10)), *p++ = (char)('0' + n - 10 * (int)udiv((uint32_t)n, 10));
  *p++ = ' ';
  return p;
}

static void say(const k6_api_t *api, st_t *s, char *p) {
  *p = 0;
  api->say(s->ln);
}

/* one tone bit into the block reader for sense k (k = 1: the chip's 1 is the 2400 Hz tone) */
static void tone(st_t *s, int k, uint32_t t) {
  uint32_t d = s->d[k] ^ t ^ (uint32_t)k, b;
  if (s->st[k] == 4) return;
  s->d[k] = (uint8_t)d;
  s->byte[k] = (uint8_t)(s->byte[k] >> 1 | d << 7);
  if (++s->nb[k] & 7) return;
  b = s->byte[k];
  if (s->st[k] == 0) { /* the first character: SOH (odd parity: 01) */
    if (b != 0x01) s->st[k] = 4;
    else s->st[k] = 1, s->soh++, s->crc[k] = 0;
    return;
  }
  {
    uint32_t c = s->crc[k] ^ b;
    int i;
    for (i = 0; i < 8; i++) c = (c >> 1) ^ (0x8408u & (0u - (c & 1)));
    s->crc[k] = c;
  }
  if (s->st[k] == 1) {
    if ((b & 0x7F) == 0x03) s->st[k] = 2; /* ETX: the check's two bytes follow */
  } else if (++s->st[k] == 4 && !s->crc[k]) s->ok++;
}

static void arm(const k6_api_t *api, uint32_t ctrl) {
  api->fd_write(0x59, (uint16_t)(ctrl | 0x4000));
  api->fd_write(0x59, (uint16_t)(ctrl | 0x1000));
  api->fd_write(0x02, 0);
}

static void setup(const k6_api_t *api, st_t *s, int n) {
  const int8_t *fe = FE[udiv((uint32_t)n, NCFG * NSYNC)];
  int m = n - (int)udiv((uint32_t)n, NCFG * NSYNC) * NCFG * NSYNC;
  const uint16_t *sy = SYNC[m - (int)udiv((uint32_t)m, NSYNC) * NSYNC];
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->tune((uint32_t)((int32_t)PROBE_HZ + 100 * fe[0]), fe[1]);
  api->fd_write(0x47, fe[2] ? (uint16_t)((s->saved[SAVED_47] & 0xF0FF) | (uint32_t)fe[2] << 8)
                            : s->saved[SAVED_47]);
  api->fd_write(0x5A, sy[0]);
  api->fd_write(0x5B, sy[1]);
  api->fd_write(0x58, CFG[udiv((uint32_t)m, NSYNC)]);
  api->fd_write(0x72, 0x60CA); /* 2400 baud x 10.32444 */
  api->fd_write(0x5C, 0x5625); /* no CRC of the chip's */
  api->fd_write(0x5D, (2 * WORDS - 1) << 8);
  api->fd_write(0x5E, 0x0204);
  arm(api, sy[2]); /* twice: a new sync word takes hold at the second */
  api->delay_us(2000);
  arm(api, sy[2]);
  api->fd_write(0x3F, 0x2002);
}

static void say_setting(const k6_api_t *api, st_t *s, int n) {
  int m = n - (int)udiv((uint32_t)n, NCFG * NSYNC) * NCFG * NSYNC;
  char *p = kv(tag(s->ln, 'v', n), "f=", udiv((uint32_t)n, NCFG * NSYNC));
  *p++ = ' ', p = hex4(p, CFG[udiv((uint32_t)m, NSYNC)]);
  *p++ = ' ', p = hex4(p, SYNC[m - (int)udiv((uint32_t)m, NSYNC) * NSYNC][0]);
  p = kv(p, " sy=", s->sy);
  p = kv(p, " soh=", s->soh);
  p = kv(p, " ok=", s->ok);
  p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(s->rmax));
  say(api, s, p);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now = 0, tkey = 0, t0 = 0, tsync = 0, total = 0, st = 0, left = 0, got = 0;
  uint16_t tw = 0;
  int cur = 0, key = 0, ctrl;
  char *p;
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(st_t); i++) ((uint8_t *)s)[i] = 0;
  for (i = 0; i < sizeof(SAVED); i++) s->saved[i] = api->fd_read(SAVED[i]);
  p = kv(s->ln, "acarsfsk start hz=", PROBE_HZ);
  p = kv(p, " slot=", SLOT_MS);
  p = kv(p, " n=", NV);
  say(api, s, p);
  setup(api, s, 0);
  for (;;) {
    ctrl = SYNC[cur - (int)udiv((uint32_t)cur, NSYNC) * NSYNC][2];
    now = api->ms();
    if (now - tkey >= 50) {
      uint16_t r = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      if (r > s->rmax) s->rmax = r;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key || now >= RUN_MS) break;
    }
    if (!left && now - t0 >= SLOT_MS) { /* a packet being read is read out first */
      say_setting(api, s, cur);
      total += s->ok;
      if (++cur == NV) cur = 0;
      s->sy = s->soh = s->ok = 0, s->rmax = 0;
      setup(api, s, cur);
      t0 = api->ms();
      continue;
    }
    if (api->fd_read(0x0C) & 1) {
      uint32_t f = api->fd_read(0x02);
      api->fd_write(0x02, 0);
      if ((f & 2) && !left) {
        tw = (uint16_t)api->ticks();
        st = api->fd_read(0x0B);
        left = WORDS, got = 0, tsync = now;
        for (i = 0; i < 2; i++) s->d[i] = 0, s->byte[i] = 0, s->st[i] = 0, s->nb[i] = 0;
        s->sy++;
      }
    }
    if (left) {
      /* a word is 20/3 ms at 2400 baud: those the chip has had for LAG word times */
      uint32_t due = udiv(((api->ticks() - tw) & 0xFFFF) * 3, 200);
      due = due > LAG ? due - LAG : 0;
      if (due > left) due = left;
      while (got < due) {
        /* its low byte was on the air first (the pager's swap), each byte's top bit first */
        uint32_t w = api->fd_read(0x5F), v = (w & 0xFF) << 8 | w >> 8;
        s->cap[got++] = (uint16_t)v;
        for (i = 16; i--;) tone(s, 0, (v >> i) & 1), tone(s, 1, (v >> i) & 1);
      }
      if (got >= left) {
        arm(api, (uint32_t)ctrl);
        left = 0;
        if (s->sy <= CAPN) {
          p = dec(tag(s->ln, 'c', cur), s->sy);
          p = kv(p, " t=", tsync);
          p = kv(p, " w=", got);
          p = kv(p, " st=", st);
          say(api, s, p);
          for (i = 0; i < got; i++) {
            uint32_t col = i - 10 * udiv(i, 10);
            if (!col) {
              p = dec(tag(s->ln, 'd', cur), s->sy);
              *p++ = ' ', *p++ = (char)('0' + udiv(i, 100));
              *p++ = (char)('0' + udiv(i, 10) - 10 * udiv(i, 100)), *p++ = '0';
            }
            *p++ = ' ', p = hex4(p, s->cap[i]);
            if (col == 9 || i + 1 == got) say(api, s, p);
          }
        }
      }
    }
    api->delay_us(left ? 500 : 2000); /* a word is 6.7 ms */
  }
  say_setting(api, s, cur);
  total += s->ok;
  for (i = 0; i < sizeof(SAVED); i++) api->fd_write(SAVED[i], s->saved[i]);
  api->fd_write(0x02, 0);
  p = kv(s->ln, "acarsfsk end ok=", total);
  say(api, s, p);
  if (!total && key) return -2;
  return (int)total;
}
