/* fskaprs: can the FD6818's own FSK modem receive APRS (Bell 202 at 1200 baud, AX.25)?

   aprsclk demodulates the IF from the 1-bit GP0 tap, whose own noise puts its threshold tens of
   dB above the chip's receiver (docs/k6-aprs-clock.md "What the model left out"). The chip's
   FSK modem sits behind the chip's own FM demodulator, and the pager's uploads prove it at
   levels the tap can't read. It was written off for AX.25 because it wants its sync word and a
   fixed length. Two ways round that, tried here on the bench channel while the IC-7100 sends
   AX.25 test packets (python3 -m modem.aprs wav OUT.wav --now --repeat 40; tools/k6_send.sh):

   stream  REG_5D = 0xFF00: on 2026-11-04 the chip passed everything it demodulated (preamble,
           sync word, payload) with a FIFO interrupt every 4 words and no sync interrupt. The
           module undoes NRZI and the bit stuffing itself and checks each frame's CRC, as a
           TNC does (NRZI: the tones' polarity doesn't matter).
   packet  the sync word set to what a run of AX.25 flags is on the line (7 bits of one tone, 1
           of the other: FE FE FE FE or 01 01 01 01), 128 bytes read on a timer after the sync
           interrupt (the pager's timed reads: a word every 13.33 ms), then through the same HDLC.

   Bell 202's tones are 1200 and 2200 Hz; the chip has 1200/1800 and 1200/2400 receivers. The
   variants (REG_58, REG_5D, sync), SLOT_MS each, round and round for RUN_S (any key ends it):
     0  3fe3 stream   FFSK 1200/1800 as the pager's F at 1200
     1  3f03 stream   the same with bits 7:4 clear (the UV-K5 messenger's AFSK 1200)
     2  33e3 stream   the 1200/2400 receiver, the 1200 baud filter
     3  33e9 stream   the 1200/2400 receiver, its own filter
     4  3fe3 packet   sync FEFE FEFE
     5  3fe3 packet   sync 0101 0101
   Lines, at the end (they fit the telemetry):
     fskaprs start hz=440850000 slot=8000
     v0 3fe3 w=1234 i=1000 rs=3 fl=345 p=12 ok=12 bad=1 r=-98
        (words read, interrupt flags seen ORed, re-arms, HDLC flags, preambles (three flags back
         to back), frames with a good CRC, flag-to-flag runs of 17 bytes or more with a bad one,
         the strongest RSSI)
     x0 fefe fefe ...     8 FIFO words as read: from the variant's first preamble, else its last
     fskaprs end ok=24
   Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 3F and 02 (the FSK modem and its interrupts), put
   back as read; the pager arms its own receiver again after any module. Receive only.
   Returns the frames with a good CRC, or -2 (a key before any), -3 (old image), -8 (scratch).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT z
#include "../k6_mod.h"

#ifndef PROBE_HZ
#define PROBE_HZ 440850000u
#endif
#ifndef RUN_S
#define RUN_S 260
#endif
#ifndef SLOT_MS
#define SLOT_MS 8000u
#endif
#define NV 6
#define MAXF 332            /* AX.25 frame bytes, with the FCS */
#define MINF 17             /* two addresses, control, PID, FCS */
#define STREAM 0xFF00u
#define PKT_WORDS 64        /* 128 bytes after a packet-mode sync */
#define WORD_US 13333u      /* 16 bits at 1200 baud */

#define IRQ_DONE (1u << 13)
#define IRQ_FULL (1u << 12)
#define IRQ_SYNC (1u << 1)
#define CTRL 0x00F8u        /* the pager's: 16 preamble bytes, 4-byte sync, no scrambler */
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u

typedef struct {
  uint16_t cfg, len, s01, s23;
} var_t;

static const var_t V[NV] = {
    {0x3FE3, STREAM, 0x076B, 0x3E36},
    {0x3F03, STREAM, 0x076B, 0x3E36},
    {0x33E3, STREAM, 0x076B, 0x3E36},
    {0x33E9, STREAM, 0x076B, 0x3E36},
    {0x3FE3, 0x7F00, 0xFEFE, 0xFEFE},
    {0x3FE3, 0x7F00, 0x0101, 0x0101},
};

static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x3F};

typedef struct {
  uint32_t w, fl, pre, ok, bad;
  uint16_t irq, rs, rmax, x[8];
  uint8_t xi, xleft, xdone; /* x: a ring until the first preamble, then 6 more words and it stays */
} acc_t;

typedef struct {
  acc_t a[NV];
  uint32_t sr, nbits, run;
  uint8_t in, last;
  uint8_t fr[MAXF];
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

static char *hex4(char *p, uint32_t v) {
  int i;
  for (i = 12; i >= 0; i -= 4) *p++ = "0123456789abcdef"[(v >> i) & 15];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v); }

/* the FCS of n bytes (CRC-16/X.25) */
static uint32_t crc16(const uint8_t *d, uint32_t n) {
  uint32_t c = 0xFFFF, i;
  int k;
  for (i = 0; i < n; i++) {
    c ^= d[i];
    for (k = 0; k < 8; k++) c = c & 1 ? (c >> 1) ^ 0x8408 : c >> 1;
  }
  return c ^ 0xFFFF;
}

/* one line bit (a tone): NRZI, flags, the stuffed zeros, a frame's CRC at its closing flag */
static void line_bit(st_t *s, acc_t *a, uint32_t tone) {
  uint32_t b = tone == s->last;
  s->last = (uint8_t)tone;
  s->sr = ((s->sr << 1) | b) & 0xFF;
  if (s->sr == 0x7E) {
    a->fl++;
    /* the flag's first seven bits went in before it was seen */
    if (s->in && s->nbits >= 7 + 8 * MINF && ((s->nbits - 7) & 7) == 0) {
      uint32_t n = (s->nbits - 7) >> 3;
      if (crc16(s->fr, n - 2) == (s->fr[n - 2] | (uint32_t)s->fr[n - 1] << 8)) a->ok++;
      else a->bad++;
    }
    s->run = s->in && s->nbits == 7 ? s->run + 1 : 0;
    if (s->run == 3) {
      if (!a->pre) a->xleft = 6;
      a->pre++;
    }
    s->in = 1, s->nbits = 0;
    return;
  }
  if ((s->sr & 0x7F) == 0x7F) {
    s->in = 0;
    return;
  }
  if (!s->in || (s->sr & 0x3F) == 0x3E) return;
  if (s->nbits < 8 * MAXF) {
    uint8_t *q = &s->fr[s->nbits >> 3], m = (uint8_t)(1u << (s->nbits & 7));
    *q = b ? (uint8_t)(*q | m) : (uint8_t)(*q & ~m);
    s->nbits++;
  } else s->in = 0;
}

/* one FIFO word: its low byte was on the air first (the pager's swap), each byte's top bit first */
static void word(const k6_api_t *api, st_t *s, acc_t *a) {
  uint32_t w = api->fd_read(0x5F), v = (w & 0xFF) << 8 | w >> 8;
  int i;
  a->w++;
  if (!a->xdone) {
    a->x[a->xi++ & 7] = (uint16_t)v;
    if (a->xleft && !--a->xleft) a->xdone = 1; /* stays from here */
  }
  for (i = 15; i >= 0; i--) line_bit(s, a, (v >> i) & 1);
}

static void arm(const k6_api_t *api) {
  api->fd_write(0x59, CTRL | CTRL_CLR);
  api->fd_write(0x59, CTRL | CTRL_EN);
  api->fd_write(0x02, 0);
}

static void setup(const k6_api_t *api, const var_t *v) {
  api->fd_write(0x3F, 0);
  api->fd_write(0x58, v->cfg);
  api->fd_write(0x72, 0x3065); /* 1200 baud */
  api->fd_write(0x5A, v->s01);
  api->fd_write(0x5B, v->s23);
  api->fd_write(0x5C, 0x5625); /* no CRC of the chip's */
  api->fd_write(0x5D, v->len);
  api->fd_write(0x5E, 0x0204); /* RX almost full at 4 words */
  arm(api);
  api->fd_write(0x3F, IRQ_DONE | IRQ_FULL | IRQ_SYNC);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now = 0, tkey = 0, tlast = 0, tw = 0, total = 0;
  uint16_t saved[sizeof(SAVED)];
  int cur = -1, left = 0, key = 0, n;
  char ln[64], *p;
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(st_t); i++) ((uint8_t *)s)[i] = 0;
  for (i = 0; i < sizeof(SAVED); i++) saved[i] = api->fd_read(SAVED[i]);
  api->tune(PROBE_HZ, 1);
  p = kv(ln, "fskaprs start hz=", PROBE_HZ);
  p = kv(p, " slot=", SLOT_MS);
  *p = 0;
  api->say(ln);
  for (;;) {
    acc_t *a;
    uint32_t f;
    now = api->ms();
    if (now - tkey >= 50) {
      tkey = now;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key || now >= RUN_S * 1000u) break;
      if (cur >= 0) {
        uint16_t r = api->fd_read(0x67) & 0x1FF;
        if (r > s->a[cur].rmax) s->a[cur].rmax = r;
      }
    }
    n = (int)(udiv(now, SLOT_MS) - NV * udiv(now, NV * SLOT_MS));
    if (n != cur) {
      cur = n, left = 0, s->in = 0, s->sr = 0, tlast = now;
      setup(api, &V[cur]);
    }
    a = &s->a[cur];
    if (api->fd_read(0x0C) & 1) {
      f = api->fd_read(0x02);
      api->fd_write(0x02, 0);
      a->irq |= (uint16_t)f;
      tlast = now;
      if (V[cur].len == STREAM) {
        if (f & IRQ_FULL)
          for (i = 0; i < 4; i++) word(api, s, a);
      } else if ((f & IRQ_SYNC) && !left) {
        left = PKT_WORDS, tw = api->ticks(); /* 100 us ticks, a 16-bit count */
        s->in = 0, s->sr = 0;
      }
      if ((f & IRQ_DONE) && !left) a->rs++, arm(api);
    }
    if (left) { /* packet mode: a word each 13.33 ms from 20 ms after the sync */
      uint32_t due = 200 + udiv((PKT_WORDS - (uint32_t)left) * WORD_US, 100);
      if (((api->ticks() - tw) & 0xFFFF) >= due) {
        word(api, s, a);
        tlast = now;
        if (!--left) a->rs++, arm(api);
      }
    } else if (now - tlast > 500) { /* nothing for half a second: listen again */
      tlast = now, a->rs++;
      arm(api);
    }
    api->delay_us(1000);
  }
  for (i = 0; i < sizeof(SAVED); i++) api->fd_write(SAVED[i], saved[i]);
  api->fd_write(0x02, 0);
  for (n = 0; n < NV; n++) {
    acc_t *a = &s->a[n];
    ln[0] = 'v', ln[1] = (char)('0' + n), ln[2] = ' ';
    p = hex4(ln + 3, V[n].cfg);
    p = kv(p, " w=", a->w);
    p = hex4(put(p, " i="), a->irq);
    p = kv(p, " rs=", a->rs);
    p = kv(p, " fl=", a->fl);
    p = kv(p, " p=", a->pre);
    p = kv(p, " ok=", a->ok);
    p = kv(p, " bad=", a->bad);
    p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(a->rmax));
    *p = 0;
    api->say(ln);
    ln[0] = 'x';
    p = ln + 2;
    for (i = 0; i < 8; i++) *p++ = ' ', p = hex4(p, a->x[(a->xi + i) & 7]);
    *p = 0;
    api->say(ln);
    total += a->ok;
  }
  p = kv(ln, "fskaprs end ok=", total);
  *p = 0;
  api->say(ln);
  if (!total && key) return -2;
  return (int)total;
}
