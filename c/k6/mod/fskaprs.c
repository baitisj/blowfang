/* fskaprs: can the FD6818's own FSK modem receive APRS (Bell 202 at 1200 baud, AX.25)?

   aprsclk demodulates the IF from the 1-bit GP0 tap, whose own noise puts its threshold tens of
   dB above the chip's receiver (docs/k6-aprs-clock.md "What the model left out"). The chip's
   FSK modem sits behind the chip's own FM demodulator, and the pager's uploads prove it at
   levels the tap can't read. It was written off for AX.25 because it wants its sync word and a
   fixed length.

   Probe 1 (2026-11-10 06:15Z, 0305b92): plain AX.25 packets, four stream settings (REG_5D =
   0xFF00) and two with the sync word set to a run of flags. Not one interrupt in 260 s at -19
   dBm: no sync, no almost-full, no RX-done. That run had no control, so this one sends what
   the chip is known to take first, and the APRS straight after it (python3 -m modem.aprs
   fskprobe OUT.wav; tools/k6_send.sh): a unit is a preamble and a sync word of the chip's own
   kind, then about a second of Bell 202 AX.25 on the same bit clock. The sync interrupt proves
   the set-up; the words after it are what the chip makes of Bell 202.

   The variants (REG_58, REG_72, REG_5D, sync), SLOT_MS each, round and round for RUN_S (any
   key ends it):
     0  3fe3 1200 baud  FFSK 1200/1800 as the pager's old F, its sync 076B 3E36, 128 bytes
                        read on the timer (a word every 13.33 ms) and put through HDLC here
     1  3f03 1200       the same with bits 7:4 clear (the UV-K5 messenger's AFSK 1200)
     2  00e9 2400       direct FSK as a 1-bit sampler of the audio: sync CCCC AACC (600 and
                        1200 Hz), then 256 bytes = the audio's sign 2400 times a second
     3  00e9 4800       the same at 4800 a second (REG_72 = c195), sync CCCC F0CC
     4  3fe3 1200       stream (REG_5D = 0xFF00, 4 words an almost-full interrupt)
     5  3fe3 1200       sync FEFE FEFE: a run of AX.25 flags (7 bits of one tone, 1 of the
                        other) once the unit's preamble has set the bit clock
   Lines, at the end (the v lines fit the telemetry; the d lines are for the serial log):
     fskaprs start hz=440850000 slot=12000
     v0 3fe3 w=1234 i=2002 sy=4 ok=12 bad=1 fl=345 p=12 r=-98
        (words read, interrupt flags seen ORed, sync interrupts, frames with a good CRC,
         flag-to-flag runs of 17 bytes or more with a bad one, HDLC flags, preambles (three
         flags back to back), the strongest RSSI)
     fskaprs end ok=24
     d0 000 fefe fefe ...  the first sync's words as read, 10 a line (stream: from about the
                           first HDLC preamble)
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
#define RUN_S 150
#endif
#ifndef SLOT_MS
#define SLOT_MS 12000u
#endif
#define NV 6
#define MAXF 332            /* AX.25 frame bytes, with the FCS */
#define MINF 17             /* two addresses, control, PID, FCS */
#define CAPW 128            /* words kept of a variant's first sync */

#define IRQ_DONE (1u << 13)
#define IRQ_FULL (1u << 12)
#define IRQ_SYNC (1u << 1)
#define CTRL 0x00F8u        /* the pager's: 16 preamble bytes, 4-byte sync, no scrambler */
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u

typedef struct {
  uint16_t cfg, baud, len, s01, s23;
  uint16_t wdiv; /* three word times in 100 us ticks: 400 at 1200 baud */
  uint8_t words; /* read by time after a sync interrupt; 0: stream, 4 an almost-full interrupt */
} var_t;

static const var_t V[NV] = {
    {0x3FE3, 0x3065, 0x7F00, 0x076B, 0x3E36, 400, 64},
    {0x3F03, 0x3065, 0x7F00, 0x076B, 0x3E36, 400, 64},
    {0x00E9, 0x60CA, 0xFF00, 0xCCCC, 0xAACC, 200, 128},
    {0x00E9, 0xC195, 0xFF00, 0xCCCC, 0xF0CC, 100, 128},
    {0x3FE3, 0x3065, 0xFF00, 0x076B, 0x3E36, 400, 0},
    {0x3FE3, 0x3065, 0x7F00, 0xFEFE, 0xFEFE, 400, 64},
};

static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x3F};

typedef struct {
  uint32_t w, fl, pre, ok, bad;
  uint16_t irq, sy, rmax, cap[CAPW];
  uint8_t ncap;
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
    if (s->run == 3) a->pre++;
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
static void word(const k6_api_t *api, st_t *s, acc_t *a, int keep) {
  uint32_t w = api->fd_read(0x5F), v = (w & 0xFF) << 8 | w >> 8;
  int i;
  a->w++;
  if (keep && a->ncap < CAPW) a->cap[a->ncap++] = (uint16_t)v;
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
  api->fd_write(0x72, v->baud);
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
  int cur = -1, left = 0, got = 0, key = 0, n;
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
    const var_t *v;
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
    if (n != cur && !left) { /* a packet being read is read out first */
      cur = n, s->in = 0, s->sr = 0, tlast = now;
      setup(api, &V[cur]);
    }
    a = &s->a[cur], v = &V[cur];
    if (api->fd_read(0x0C) & 1) {
      f = api->fd_read(0x02);
      api->fd_write(0x02, 0);
      a->irq |= (uint16_t)f;
      tlast = now;
      if (!v->words) {
        if (f & IRQ_FULL) {
          if (!a->pre && a->ncap > CAPW - 4) a->ncap = 0; /* round again until a preamble */
          for (i = 0; i < 4; i++) word(api, s, a, 1);
        }
      } else if ((f & IRQ_SYNC) && !left) {
        left = v->words, got = 0, tw = api->ticks(); /* 100 us ticks, a 16-bit count */
        s->in = 0, s->sr = 0;
        a->sy++;
      }
      /* stream: the chip stopped at REG_5D's length. By the timer the read is over before the
         chip's own RX-done, and re-arming on that one, or on an idle one, can land in the next
         preamble (the pager's idlekeep) */
      if ((f & IRQ_DONE) && !v->words) arm(api);
    }
    if (left) { /* the pager's timed reads: a word at the sync interrupt, then one a word time */
      uint32_t due = 1 + udiv(((api->ticks() - tw) & 0xFFFF) * 3, v->wdiv);
      if (due > v->words) due = v->words;
      while ((uint32_t)got < due) word(api, s, a, a->sy == 1), got++;
      tlast = now;
      if (got >= left) left = 0, arm(api);
    } else if (!v->words && now - tlast > 500) { /* stream, half a second of nothing: listen again */
      tlast = now;
      arm(api);
    }
    api->delay_us(v->words > 64 ? 300 : 1000);
  }
  for (i = 0; i < sizeof(SAVED); i++) api->fd_write(SAVED[i], saved[i]);
  api->fd_write(0x02, 0);
  for (n = 0; n < NV; n++) {
    acc_t *a = &s->a[n];
    ln[0] = 'v', ln[1] = (char)('0' + n), ln[2] = ' ';
    p = hex4(ln + 3, V[n].cfg);
    p = kv(p, " w=", a->w);
    p = hex4(put(p, " i="), a->irq);
    p = kv(p, " sy=", a->sy);
    p = kv(p, " ok=", a->ok);
    p = kv(p, " bad=", a->bad);
    p = kv(p, " fl=", a->fl);
    p = kv(p, " p=", a->pre);
    p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(a->rmax));
    *p = 0;
    api->say(ln);
    total += a->ok;
  }
  p = kv(ln, "fskaprs end ok=", total);
  *p = 0;
  api->say(ln);
  for (n = 0; n < NV; n++) { /* the words, for the serial log (the telemetry is full by now) */
    acc_t *a = &s->a[n];
    for (i = 0; i < a->ncap; i++) {
      if (udiv(i, 10) * 10 == i) {
        ln[0] = 'd', ln[1] = (char)('0' + n), ln[2] = ' ';
        ln[3] = (char)('0' + udiv(i, 100)), ln[4] = (char)('0' + udiv(i, 10) - 10 * udiv(i, 100));
        ln[5] = '0', p = ln + 6;
      }
      *p++ = ' ', p = hex4(p, a->cap[i]);
      if (udiv(i + 1, 10) * 10 == i + 1 || i + 1 == a->ncap) {
        *p = 0;
        api->say(ln);
        api->delay_us(10000);
      }
    }
  }
  if (!total && key) return -2;
  return (int)total;
}
