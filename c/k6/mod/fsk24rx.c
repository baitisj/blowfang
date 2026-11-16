/* fsk24rx: can the FD6818's own FSK modem receive plain (direct) FSK at 2400 baud? A probe.

   The K6 sends plain FSK at 2400 baud fine (its CAL burst, 2026-11-04, decoded on the IC-7100),
   but nothing it ever received at 2400 synced: the pager's D/2400, F/2400 and POCSAG 2400. All of
   those came from the IC-7100's USB audio, whose transmit path pre-emphasises and cuts below
   about 300 Hz (docs/uv380-c6000-modem.md, C2), and plain FSK carries its data down to DC. The
   RT-950 Pro's BK4829 (the same family) received the chip's 2400 baud FSK 50 of 50 from a flat
   transmitter (a bladeRF) with its RX 300 Hz high-pass off (FerriteFW's audio limits note,
   2026-10-27; facts only). So this module listens with the receive filters off in steps, while
   the bench sends test packets pre-equalised several ways (python3 -m modem.fsk24 wav), and says
   which way and which setting reads them (python3 -m modem.fsk24 report LOG).

   A test packet is the pager's framing (0x55 preamble, sync 07 6B 3E 36) and 64 bytes:
     0 0xF2   1 variant (the bench's pre-equaliser and level)   2 sequence
     3..61    PN9 (x^9 + x^5 + 1 from 0x1FF, the low byte of the state a byte), the same every
              packet, so the bits wrong can be counted even when the CRC fails
     62..63   CRC-16/CCITT-FALSE of bytes 0..61, big-endian (as the chip's own CRC)
   The chip is in packet mode (REG_5D: 64 bytes after the sync) and its words are read by time,
   LAG words behind, at REG_72's rate. Settings of FE_S seconds after WAIT_MS, REG_2B's DC
   cancellation (bit 14) always off (the pager's D/1200 went from 7/28 to 31/31 with it off):
      0  00e1  1200 baud  2B +0000   the pager's old D/1200 recipe: the control
      1  00e9  2400       +0000      RX bandwidth bits 4 (FSK 2.4K), filters as found
      2  00e9  2400       +0400      RX 300 Hz high-pass off (the RT-950 note's data set-up)
      3  00e9  2400       +0700      high-pass, 3 kHz low-pass and de-emphasis off
      4  00e1  2400       +0400      RX bandwidth bits 0 (FSK 1.2K), high-pass off
   Lines (each up to 60 characters):
     fsk24 start hz=440850000 n=5
     s 2 t=30000 cfg=00e9 b=60ca a=04          a setting: REG_58, REG_72, REG_2B bits >> 8
     p 2 v=3 q=17 c=1 i=0 e=0 x=0 r=-41        a packet: setting, variant, sequence, CRC right,
                                               upside down, PN9 bits wrong of 472, reads that
                                               looked like the empty FIFO, RSSI
     d 2 00 0000 1111 ...                      the words of a packet whose CRC failed with
                                               fewer than E_DUMP bits wrong (the first DUMPN a
                                               setting), 10 a line: slips show as a shifted run
     v 2 n=24 ok=20 z=1                        a setting's end: packets, CRC right, junk (more
                                               than E_JUNK bits wrong)
     fsk24 end n=96 ok=80
   AF_IRQ (bit 0 of a setting's REG_2B bits, not written to REG_2B): read the FIFO on the chip's
   interrupts (polled: REG_0C bit 0, flags in REG_02), IRQ_WORDS a FIFO-almost-full and the rest
   at RX-done, as the pager reads F/1800, not by time. x= then counts words padded or repeated.
   F24_ABS: the settings start on a fixed grid of FE_S from the first, not FE_S after the
   last ended (a packet being read holds a change back), so a bench signal can be timed to them
   from the "s 0" line (fsk24rx_sweep.c).
   Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 2B, 3F and 02, put back as read. Receive only.
   Returns the packets with the CRC right, or -3 (old image), -8 (scratch). Any key ends it.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 2
#ifndef K6_MODULE_OPT
#define K6_MODULE_OPT z
#endif
#include "../k6_mod.h"

#ifndef F24_HZ
#define F24_HZ 440850000u
#endif
#ifndef WAIT_MS
#define WAIT_MS 30000u
#endif
#ifndef FE_S
#define FE_S 30u
#endif
#ifndef SLOT_SCALE
#define SLOT_SCALE 1000u
#endif
#ifndef LAG
#define LAG 4u
#endif
#define NW 32u            /* words a packet */
#define NB 64u
#define PN_FIRST 3u
#define PN_N 59u          /* bytes 3..61: 472 bits */
#define E_DUMP 120u
#define E_JUNK 120u
#ifndef DUMPN
#define DUMPN 3u
#endif
#define CTRL 0x00F8u      /* 16 preamble bytes, 4-byte sync, no scrambler (the pager's) */
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u
#define AF_DC_CANCEL 0x4000u
#define AF_IRQ 0x0001u    /* in a setting's REG_2B bits: read on the chip's interrupts (not REG_2B) */
#define IRQ_DONE (1u << 13)
#define IRQ_FULL (1u << 12)
#define IRQ_SYNC (1u << 1)
#define IRQ_WORDS 3u      /* words a FIFO-almost-full interrupt (the pager's F/1800: 5e=0203) */
#define FIFO_WORDS 8u

typedef struct {
  uint16_t cfg, r72, wt, af; /* REG_58, REG_72, a word in 1/16 ticks, REG_2B bits */
} var_t;

#ifdef F24_V
#define NV F24_NV
static const var_t V[NV] = {F24_V};
#else
#define NV 5
static const var_t V[NV] = {
    {0x00E1, 12389, 2133, 0x0000},
    {0x00E9, 24779, 1067, 0x0000},
    {0x00E9, 24779, 1067, 0x0400},
    {0x00E9, 24779, 1067, 0x0700},
    {0x00E1, 24779, 1067, 0x0400},
};
#endif

static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F};
#define NSV sizeof(SAVED)
#define SAVED_2B 8

typedef struct {
  uint16_t sv[NSV];
  uint16_t w[NW];
  uint8_t b[NB];
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

static uint32_t clk(const k6_api_t *api, st_t *s) {
  uint16_t t = (uint16_t)api->ticks();
  s->el += (uint16_t)(t - s->tl);
  s->tl = t;
  return s->el;
}

/* clear and arm the modem for the next packet; returns the word an empty FIFO reads as */
static uint32_t seek(const k6_api_t *api, const st_t *s, const var_t *v) {
  uint32_t i, w = 0;
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x2B, (uint16_t)((s->sv[SAVED_2B] & ~AF_DC_CANCEL) | (v->af & ~AF_IRQ)));
  api->fd_write(0x5A, 0x076B);
  api->fd_write(0x5B, 0x3E36);
  api->fd_write(0x58, v->cfg);
  api->fd_write(0x72, v->r72);
  api->fd_write(0x5C, 0x5625); /* the chip's CRC off */
  api->fd_write(0x5D, (uint16_t)((NB - 1) << 8));
  api->fd_write(0x5E, v->af & AF_IRQ ? 0x0200u | IRQ_WORDS : 0x0204u);
  for (i = 0; i < 2; i++) {
    api->fd_write(0x59, CTRL | CTRL_CLR);
    api->fd_write(0x59, CTRL | CTRL_EN);
    api->fd_write(0x02, 0);
    if (!i) api->delay_us(2000);
  }
  for (i = 0; i < 8; i++) w = api->fd_read(0x5F);
  if (v->af & AF_IRQ) {
    api->fd_write(0x02, 0);
    api->fd_write(0x3F, IRQ_DONE | IRQ_FULL | IRQ_SYNC);
  }
  return w;
}

static uint32_t crc16(const uint8_t *b, uint32_t n) {
  uint32_t c = 0xFFFF, i, k;
  for (i = 0; i < n; i++) {
    c ^= (uint32_t)b[i] << 8;
    for (k = 0; k < 8; k++) c = c & 0x8000 ? (c << 1) ^ 0x1021 : c << 1;
  }
  return c & 0xFFFF;
}

static uint32_t ones(uint32_t x) {
  uint32_t n = 0;
  while (x) x &= x - 1, n++;
  return n;
}

/* PN9 bits wrong in bytes PN_FIRST.. against the test pattern */
static uint32_t pn_errors(const uint8_t *b) {
  uint32_t st = 0x1FF, i, k, e = 0;
  for (i = 0; i < PN_N; i++) {
    e += ones((b[PN_FIRST + i] ^ st) & 0xFF);
    for (k = 0; k < 8; k++) st = st >> 1 | ((st ^ st >> 5) & 1) << 8;
  }
  return e;
}

static void s_line(const k6_api_t *api, st_t *s, int cur, uint32_t now) {
  char *p = kv(s->ln, "s ", (uint32_t)cur);
  p = kv(p, " t=", now);
  p = hexn(put(p, " cfg="), V[cur].cfg, 4);
  p = hexn(put(p, " b="), V[cur].r72, 4);
  p = hexn(put(p, " a="), V[cur].af >> 8, 2);
  say(api, s, p);
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, k, now, tkey = 0, tset, prev, t0 = 0, rn = 0, nx = 0, rpk = 0;
  uint32_t n = 0, ok = 0, nz = 0, ndump = 0, tn = 0, tok = 0;
  int key = 0, cur = 0, cap = 0;
  char *p;
  const var_t *v = &V[0];
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < NSV; i++) s->sv[i] = api->fd_read(SAVED[i]);
  s->el = 0, s->tl = (uint16_t)api->ticks();
  api->tune(F24_HZ, 1);
  p = kv(s->ln, "fsk24 start hz=", F24_HZ);
  p = kv(p, " n=", NV);
  say(api, s, p);
  while (api->ms() < WAIT_MS && !key) {
    api->delay_us(50000);
    key = api->level >= 4 ? api->key_poll() : api->key_down();
  }
  tset = api->ms();
  s_line(api, s, 0, tset);
  prev = seek(api, s, v);
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
      p = kv(p, " n=", n);
      p = kv(p, " ok=", ok);
      p = kv(p, " z=", nz);
      say(api, s, p);
      tn += n, tok += ok, n = ok = nz = ndump = 0;
      if (++cur >= NV) break;
#ifdef F24_ABS
      v = &V[cur], tset += FE_S * SLOT_SCALE; /* slots on a fixed grid from the first (a sweep) */
#else
      v = &V[cur], tset = now;
#endif
      s_line(api, s, cur, now);
      prev = seek(api, s, v);
    }
    if (v->af & AF_IRQ) {
      if (api->fd_read(0x0C) & 1) {
        uint32_t f = api->fd_read(0x02), m = 0;
        api->fd_write(0x02, 0);
        if ((f & (IRQ_SYNC | IRQ_FULL)) && !cap) cap = 1, rn = 0, nx = 0, rpk = 0;
        if (cap && (f & IRQ_FULL)) m = IRQ_WORDS;
        if (cap && (f & IRQ_DONE)) m = NW; /* the rest: at most the FIFO's 8 (a short packet fails) */
        if (m > NW - rn) m = NW - rn;
        if (m > FIFO_WORDS && (f & IRQ_DONE)) m = FIFO_WORDS;
        while (m--) {
          uint32_t w = api->fd_read(0x5F);
          if (rn >= 8 && w == s->w[rn - 8]) nx++;
          s->w[rn++] = (uint16_t)w;
        }
        if (cap && (f & IRQ_DONE)) while (rn < NW) s->w[rn++] = 0, nx++;
      }
    } else if (!cap) {
      uint32_t w = api->fd_read(0x5F);
      if (w != prev) {
        cap = 1, rn = 1, nx = 0, rpk = 0;
        t0 = clk(api, s) << 4;
        s->w[0] = (uint16_t)w;
      }
    }
    while (!(v->af & AF_IRQ) && cap && rn < NW && (int32_t)((clk(api, s) << 4) - t0 - (rn + LAG) * v->wt) >= 0) {
      uint32_t w = api->fd_read(0x5F);
      if (rn >= 8 && w == s->w[rn - 8]) nx++; /* as the empty FIFO reads (or by chance) */
      s->w[rn++] = (uint16_t)w;
    }
    if (cap && rn >= NW) {
      uint32_t e, inv = 0, good;
      cap = 0;
      for (i = 0; i < NW; i++) s->b[2 * i] = (uint8_t)s->w[i], s->b[2 * i + 1] = (uint8_t)(s->w[i] >> 8);
      e = pn_errors(s->b);
      if (e > PN_N * 4) { /* upside down */
        for (i = 0; i < NB; i++) s->b[i] = (uint8_t)~s->b[i];
        e = PN_N * 8 - e, inv = 1;
      }
      good = crc16(s->b, NB - 2) == ((uint32_t)s->b[NB - 2] << 8 | s->b[NB - 1]);
      n++, ok += good, nz += e > E_JUNK;
      p = kv(s->ln, "p ", (uint32_t)cur);
      p = kv(p, " v=", s->b[1]);
      p = kv(p, " q=", s->b[2]);
      p = kv(p, " c=", good);
      p = kv(p, " i=", inv);
      p = kv(p, " e=", e);
      p = kv(p, " x=", nx);
      p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(rpk));
      say(api, s, p);
      if (!good && e < E_DUMP && ndump < DUMPN) {
        ndump++;
        for (i = 0, k = 0; i < NW; i++, k = k == 9 ? 0 : k + 1) {
          if (!k) {
            p = kv(s->ln, "d ", (uint32_t)cur);
            *p++ = ' ', p = hexn(p, i, 2);
          }
          *p++ = ' ', p = hexn(p, s->w[i], 4);
          if (k == 9 || i + 1 == NW) say(api, s, p);
        }
      }
      prev = seek(api, s, v);
    }
    api->delay_us(300);
  }
  for (i = 0; i < NSV; i++) api->fd_write(SAVED[i], s->sv[i]);
  api->fd_write(0x02, 0);
  tn += n, tok += ok;
  p = kv(s->ln, "fsk24 end n=", tn);
  p = kv(p, " ok=", tok);
  say(api, s, p);
  return (int)tok;
}
