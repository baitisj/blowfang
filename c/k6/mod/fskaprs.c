/* fskaprs: can the FD6818's own FSK modem receive APRS (Bell 202 at 1200 baud, AX.25)?

   aprsclk demodulates the IF from the 1-bit GP0 tap, whose own noise puts its threshold tens of
   dB above the chip's receiver (docs/k6-aprs-clock.md "What the model left out"). The chip's
   FSK modem sits behind the chip's own FM demodulator, and the pager's uploads prove it at
   levels the tap can't read. It was written off for AX.25 because it wants its sync word and a
   fixed length.

   Probe 1 (2026-11-10 06:15Z): plain AX.25 packets: not one interrupt. Probe 2 (06:57Z,
   cf6caff): after a preamble and sync word of its own kind the FFSK receiver (REG_58 = 3fe3,
   1200 baud) follows Bell 202 bit for bit, but as out[i] = sent[i] AND sent[i+1] (1 = the
   1200 Hz tone): every run of 1200 Hz a bit short, a lone bit of it gone, so no frame passes
   its CRC. This is probe 3, for what gives that bit back and what starts the receiver on a
   plain APRS packet. Its test signal (python3 -m modem.aprs fskprobe OUT.wav) is a round of
   units: P0 to P3 (the pager's preamble and sync at 1200 baud, a word naming the unit, then
   Bell 202 AX.25 a quarter of a bit later each), C (the same with the APRS at the chip's own
   1200/1800 Hz, which must decode), F24 (preamble and sync at 2400 baud), A+ and A- (a plain
   APRS packet after 40 flags, the flags' long tone 1200 or 2200 Hz).

   The variants, SLOT_MS each from the first sync heard, once round (any key ends it):
      0  3fe3 1200 baud, the pager's sync 076B 3E36, 128 bytes: probe 2's, against P0 to P3
      1  the same, and REG_72 to 2400 baud at the sync interrupt: two samples a bit
      2  to 3600 baud
      3  to 4800 baud
      4  3fe1 (the setting that takes 2400 baud), to 2400 baud at the sync
      5  3fe1 at 2400 baud from the start (unit F24)
      6  sync 3F3F 3F3F: what probe 2 read of a run of flags whose long tone is 1200 Hz
      7  sync 3F3F, two bytes (REG_59 = 00f0)
      8  sync 0000, two bytes: flags whose long tone is 2200 Hz read as all zeros
      9  as 7, and to 2400 baud at the sync
     10  3fe1  \
     11  3fe7   } REG_58's RX bandwidth bits 3:1 (3fe3 has 1) and RX gain bits 9:8 at 1200 baud
     12  3ce3  /
     13  3fe3 with REG_2B + 0100 (the RX de-emphasis off)
     14  3fe3 with REG_2B + 0700 (and the 3 kHz low-pass and 300 Hz high-pass)
     15  3fe3, and REG_58 to 33e3 at the sync (the 1200/2400 Hz receiver, which never syncs)
     16  to 33e9
     17  to 3fe9 (its bandwidth alone)
   A packet's words are read by time, two word times behind the chip, and said as it ends (the
   telemetry is full after the first few lines: read the serial log):
     fskaprs start hz=440850000 slot=12000
     c00 1 t=12345 dn=853 w=64 i=3002   variant, its capture, ms at the sync interrupt, ms from
                                        there to RX-done (0: none came), words, interrupts seen
     d00 1 000 1001 fefe ...            the words as read, 10 a line: the unit's name, the line
     v00 3fe3 w=384 sy=6 ok=1 bad=0 fl=75 r=-19   at the slot's end: words, syncs, frames with a
                                        good CRC and a bad one, HDLC flags, the strongest RSSI
     fskaprs end ok=3
   Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 2B, 3F and 02 (the FSK modem, its interrupts, the
   AF filters), put back as read; the pager arms its own receiver again after any module.
   Receive only. Returns the frames with a good CRC, or -2 (a key before any), -3 (old image),
   -8 (scratch).
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#define K6_MODULE_OPT z
#include "../k6_mod.h"

#ifndef PROBE_HZ
#define PROBE_HZ 440850000u
#endif
#ifndef SLOT_MS
#define SLOT_MS 12000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 40000u      /* for the first sync: the sender starts after the upload's reply */
#endif
#define NV 18
#define MAXF 332            /* AX.25 frame bytes, with the FCS */
#define MINF 17             /* two addresses, control, PID, FCS */
#define CAPW 128            /* words in the longest packet (REG_5D's 256 bytes) */
#define CAPN 9              /* captures said per variant */
#define LAG 2               /* words the timed reads stay behind the chip (its FIFO holds 8) */

#define IRQ_DONE (1u << 13)
#define IRQ_FULL (1u << 12)
#define IRQ_SYNC (1u << 1)
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u
#define B1200 0x3065u       /* REG_72 = baud x 10.32444 */
#define B2400 0x60CAu
#define B3600 0x9130u
#define B4800 0xC195u

typedef struct {
  uint16_t cfg, baud, s01, s23;
  uint16_t af;    /* ORed into REG_2B */
  uint16_t cfg2;  /* REG_58 at the sync interrupt (0: as it is) */
  uint16_t baud2; /* REG_72 at the sync interrupt (0: as it is) */
  uint16_t wdiv;  /* three word times after the sync in 100 us ticks: 400 at 1200 baud */
  uint8_t ctrl;   /* REG_59: f8 = the pager's (16 preamble bytes, 4-byte sync), f0 = 2-byte */
  uint8_t words;  /* the packet (REG_5D = its bytes - 1) */
} var_t;

static const var_t V[NV] = {
    {0x3FE3, B1200, 0x076B, 0x3E36, 0, 0, 0, 400, 0xF8, 64},
    {0x3FE3, B1200, 0x076B, 0x3E36, 0, 0, B2400, 200, 0xF8, 128},
    {0x3FE3, B1200, 0x076B, 0x3E36, 0, 0, B3600, 133, 0xF8, 128},
    {0x3FE3, B1200, 0x076B, 0x3E36, 0, 0, B4800, 100, 0xF8, 128},
    {0x3FE1, B1200, 0x076B, 0x3E36, 0, 0, B2400, 200, 0xF8, 128},
    {0x3FE1, B2400, 0x076B, 0x3E36, 0, 0, 0, 200, 0xF8, 128},
    {0x3FE3, B1200, 0x3F3F, 0x3F3F, 0, 0, 0, 400, 0xF8, 64},
    {0x3FE3, B1200, 0x3F3F, 0x3F3F, 0, 0, 0, 400, 0xF0, 64},
    {0x3FE3, B1200, 0x0000, 0x0000, 0, 0, 0, 400, 0xF0, 64},
    {0x3FE3, B1200, 0x3F3F, 0x3F3F, 0, 0, B2400, 200, 0xF0, 128},
    {0x3FE1, B1200, 0x076B, 0x3E36, 0, 0, 0, 400, 0xF8, 64},
    {0x3FE7, B1200, 0x076B, 0x3E36, 0, 0, 0, 400, 0xF8, 64},
    {0x3CE3, B1200, 0x076B, 0x3E36, 0, 0, 0, 400, 0xF8, 64},
    {0x3FE3, B1200, 0x076B, 0x3E36, 0x0100, 0, 0, 400, 0xF8, 64},
    {0x3FE3, B1200, 0x076B, 0x3E36, 0x0700, 0, 0, 400, 0xF8, 64},
    {0x3FE3, B1200, 0x076B, 0x3E36, 0, 0x33E3, 0, 400, 0xF8, 64},
    {0x3FE3, B1200, 0x076B, 0x3E36, 0, 0x33E9, 0, 400, 0xF8, 64},
    {0x3FE3, B1200, 0x076B, 0x3E36, 0, 0x3FE9, 0, 400, 0xF8, 64},
};

static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F};
#define SAVED_2B 8

typedef struct {
  uint32_t w, fl, ok, bad; /* the variant now */
  uint32_t sr, nbits;
  uint16_t sy, rmax, irq, cap[CAPW];
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
static void line_bit(st_t *s, uint32_t tone) {
  uint32_t b = tone == s->last;
  s->last = (uint8_t)tone;
  s->sr = ((s->sr << 1) | b) & 0xFF;
  if (s->sr == 0x7E) {
    s->fl++;
    /* the flag's first seven bits went in before it was seen */
    if (s->in && s->nbits >= 7 + 8 * MINF && ((s->nbits - 7) & 7) == 0) {
      uint32_t n = (s->nbits - 7) >> 3;
      if (crc16(s->fr, n - 2) == (s->fr[n - 2] | (uint32_t)s->fr[n - 1] << 8)) s->ok++;
      else s->bad++;
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
static void word(const k6_api_t *api, st_t *s, int at, int hdlc) {
  uint32_t w = api->fd_read(0x5F), v = (w & 0xFF) << 8 | w >> 8;
  int i;
  s->w++;
  s->cap[at] = (uint16_t)v;
  if (hdlc)
    for (i = 15; i >= 0; i--) line_bit(s, (v >> i) & 1);
}

static void arm(const k6_api_t *api, const var_t *v) {
  api->fd_write(0x59, v->ctrl | CTRL_CLR);
  api->fd_write(0x59, v->ctrl | CTRL_EN);
  api->fd_write(0x02, 0);
}

/* Probe 2's last variant took its first packet on the variant before's sync word (REG_58 and
   REG_72 were the same in both), so REG_58 goes off and on around the new one, and the receiver
   is armed twice */
static void setup(const k6_api_t *api, const var_t *v, uint32_t af) {
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x2B, af | v->af);
  api->fd_write(0x5A, v->s01);
  api->fd_write(0x5B, v->s23);
  api->fd_write(0x58, v->cfg);
  api->fd_write(0x72, v->baud);
  api->fd_write(0x5C, 0x5625); /* no CRC of the chip's */
  api->fd_write(0x5D, (uint32_t)(2 * v->words - 1) << 8);
  api->fd_write(0x5E, 0x0204); /* RX almost full at 4 words (seen in i=, not acted on) */
  arm(api, v);
  api->delay_us(2000);
  arm(api, v);
  api->fd_write(0x3F, IRQ_DONE | IRQ_FULL | IRQ_SYNC);
}

/* "<c>NN " */
static char *tag(char *p, char c, int n) {
  *p++ = c, *p++ = (char)('0' + udiv((uint32_t)n, 10)), *p++ = (char)('0' + n - 10 * (int)udiv((uint32_t)n, 10));
  *p++ = ' ';
  return p;
}

static void say_variant(const k6_api_t *api, st_t *s, int n) {
  char ln[64], *p = hex4(tag(ln, 'v', n), V[n].cfg);
  p = kv(p, " w=", s->w);
  p = kv(p, " sy=", s->sy);
  p = kv(p, " ok=", s->ok);
  p = kv(p, " bad=", s->bad);
  p = kv(p, " fl=", s->fl);
  p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(s->rmax));
  *p = 0;
  api->say(ln);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now = 0, tkey = 0, t0 = 0, tsync = 0, tend = 0, dn = 0, total = 0, af;
  uint16_t saved[sizeof(SAVED)], tw = 0;
  int cur = 0, left = 0, got = 0, key = 0, started = 0;
  char ln[64], *p;
  const var_t *v = &V[0];
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(st_t); i++) ((uint8_t *)s)[i] = 0;
  for (i = 0; i < sizeof(SAVED); i++) saved[i] = api->fd_read(SAVED[i]);
  af = saved[SAVED_2B];
  api->tune(PROBE_HZ, 1);
  p = kv(ln, "fskaprs start hz=", PROBE_HZ);
  p = kv(p, " slot=", SLOT_MS);
  *p = 0;
  api->say(ln);
  setup(api, v, af);
  for (;;) {
    now = api->ms();
    if (now - tkey >= 50) {
      uint16_t r = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      if (r > s->rmax) s->rmax = r;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key || (!started && now >= WAIT_MS)) break;
    }
    if (started && !left && now - t0 >= SLOT_MS) { /* a packet being read is read out first */
      say_variant(api, s, cur);
      total += s->ok;
      if (++cur == NV) break;
      v = &V[cur], t0 = api->ms();
      s->w = s->fl = s->ok = s->bad = 0, s->sy = s->rmax = 0;
      setup(api, v, af);
    }
    if (api->fd_read(0x0C) & 1) {
      uint32_t f = api->fd_read(0x02);
      api->fd_write(0x02, 0);
      if (left) s->irq |= (uint16_t)f;
      /* an RX-done with no packet being read is the last one's own, late, or an idle one:
         re-arming on it can land in the next preamble (the pager's idlekeep) */
      if ((f & IRQ_SYNC) && !left) {
        tw = (uint16_t)api->ticks(); /* 100 us ticks, a 16-bit count */
        if (v->cfg2) api->fd_write(0x58, v->cfg2);
        if (v->baud2) api->fd_write(0x72, v->baud2);
        left = v->words, got = 0, tsync = now, tend = 0, dn = 0;
        s->irq = (uint16_t)f, s->in = 0, s->sr = 0;
        s->sy++;
        if (!started) started = 1, t0 = now;
      } else if ((f & IRQ_DONE) && left && !dn) dn = now - tsync + !(now - tsync);
    }
    if (left) {
      uint32_t due = udiv(((api->ticks() - tw) & 0xFFFF) * 3, v->wdiv);
      due = due > LAG ? due - LAG : 0;
      if (due > (uint32_t)left) due = (uint32_t)left;
      while ((uint32_t)got < due) word(api, s, got, !v->baud2), got++;
      if (got >= left && !tend) tend = now;
      /* read out: the chip's RX-done is LAG words old by now, or isn't coming */
      if (tend && (dn || now - tend > 300)) {
        if (v->cfg2) api->fd_write(0x58, v->cfg);
        if (v->baud2) api->fd_write(0x72, v->baud);
        arm(api, v);
        if (s->sy <= CAPN) {
          p = dec(tag(ln, 'c', cur), s->sy);
          p = kv(p, " t=", tsync);
          p = kv(p, " dn=", dn);
          p = kv(p, " w=", (uint32_t)got);
          p = hex4(put(p, " i="), s->irq);
          *p = 0;
          api->say(ln);
          for (i = 0; i < (uint32_t)got; i++) {
            uint32_t col = i - 10 * udiv(i, 10);
            if (!col) {
              p = dec(tag(ln, 'd', cur), s->sy);
              *p++ = ' ', *p++ = (char)('0' + udiv(i, 100));
              *p++ = (char)('0' + udiv(i, 10) - 10 * udiv(i, 100)), *p++ = '0';
            }
            *p++ = ' ', p = hex4(p, s->cap[i]);
            if (col == 9 || i + 1 == (uint32_t)got) {
              *p = 0;
              api->say(ln);
            }
          }
        }
        left = 0;
      }
    }
    api->delay_us(left && v->wdiv < 400 ? 300 : 2000); /* a word is 3.3 ms at 4800 baud, 13.3 at 1200 */
  }
  for (i = 0; i < sizeof(SAVED); i++) api->fd_write(SAVED[i], saved[i]);
  api->fd_write(0x02, 0);
  if (started && cur < NV) say_variant(api, s, cur), total += s->ok; /* a key, mid-slot */
  p = kv(ln, "fskaprs end ok=", total);
  *p = 0;
  api->say(ln);
  if (!total && key) return -2;
  return (int)total;
}
