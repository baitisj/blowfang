/* pocsag: POCSAG pager receiver on the FD6818's plain FSK receiver (docs/k6-pocsag.md).

   POCSAG is direct FSK at 512, 1200 or 2400 baud: behind the FM discriminator a plain NRZ line,
   which is what the chip's plain FSK receiver (REG_58 RX mode 0, 00E9) slices on a clock that
   follows the data's transitions. At 512 and 2400 baud it runs at the line's own baud and hands
   over the pager's bits themselves, the top bit of each byte first, the first byte in a FIFO
   word's low byte. At 1200 baud the chip at the line's baud drops bits (bench run 1), so it runs
   at 4800 as a sampler (as the APRS work used it, aprsclk.c) with the AF path as found, and the
   module slices the samples itself (bench run 2, docs/k6-pocsag.md): a run of one sample is
   chatter, shared out between the runs either side (or joined to them when they are alike), and
   a run of l samples is (l + carry + 2) / 4 bits, at least one, the remainder carried on to the
   next run (unless l is a whole number of bits: then the run is taken as it is).

   Hunting: the chip's sync word is AAAA (sixteen bits of the preamble, 1010...; at 1200 baud
   F0F0, four of the preamble's bits sampled) at each baud in turn, HUNT_MS each, so the chip
   starts inside a preamble at whatever baud it is sent; the preamble's words then go by and the
   module finds the batch sync codeword 7CD215D8 in the bits itself, either way up (no wrong bit
   or up to two), at any bit offset. A capture whose first words are no preamble (four words
   unlike AAAA, or at 1200 baud PRE_REP bits like the one before), or that finds no sync codeword
   in PRE_W words' worth of bits, is a false start (counted, not said).

   Reading: the FIFO (eight words) is read at the chip's heels, again and again. An empty FIFO
   takes nothing and gives the word in the slot it would read next, the one taken eight before
   the next (the APRS bench, docs/k6-aprs-clock.md), so between two words the reads give one
   word over and over, and a word that comes shows as a read unlike it (or as the next slot's
   word, unlike it, once the new word is taken). The word's time follows from when that was
   (the chip's clock is learnt: the sampler's text comes 1.75% slower than its preamble, the
   bench, run 4); a word alike the one eight before, coming between the next slot's alike too
   (a preamble's words, idle codewords), is taken by that clock alone. Where a word unlike the
   empty FIFO's may have been either (that word, or the word alike the one eight before and then
   the next slot's), the next read tells most; the rest the empty FIFO tells seven words on,
   when it gives that slot's word itself, and so the words go to the decoder eight behind.

   Decoding: a codeword's BCH(31,21) check and parity put up to two wrong bits right (syndromes of
   the 31 single-bit errors, worked out at the start, and their pairs). After sixteen codewords the
   next 32 bits must be the sync codeword again (up to four wrong bits), else the hunt for it
   starts over; two batches' worth of bits without one ends the transmission. A capture is the
   chip's packet (REG_5D: 512 bytes at 512 baud, 2048 at 2400, about 7 s; 2048 bytes of samples
   at 1200, 3.4 s); when it ends inside a transmission the chip's sync word becomes the codeword's
   last two bytes (15D8, or EA27 upside down; for the sampler F0F0 again, and the hunt for the
   next sync codeword) and the next batch opens a capture that goes on with the same messages.

   Lines (after a transmission, never during one: a line costs milliseconds of listening):
     pocsag start hz=439987500 n=1 tune=1 text=1
     s 0 t=32000 cfg=00e9 a=0000         a setting: REG_58, the bits set in REG_2B (besides
                                         de-emphasis off at 512 and 2400 baud)
     p 3 s=0 b=1200 t=40012 w=180 i=0 e=2 r=-41
                                         a transmission: its number, setting, baud, ms at its
                                         first word, words read, upside down, words the empty FIFO put
                                         right, RSSI peak
     k 3 q=4 ok=60 f=3 x=1 c=1 m=3       sync codewords, codewords right as read, put right, past
                                         help; captures it took; messages
     m 1234 3 e0 AG7EW TEST 1 1200       a message: address, function, codewords lost, its text (0
                                         numeric, else alphanumeric); a long one goes on in
     + ...                               lines of its own
     v 0 n=12 m=30 z=4                   a setting's end: transmissions, messages, false starts
     pocsag end n=12 m=30 z=4
   The m and + lines exist only in a build for the amateur band (POC_HZ 430-450 MHz, e.g. DAPNET on
   439.9875): elsewhere a page's text is protected (US ECPA), and the code that would say it is
   not in the module at all. Writes REG_58, 59, 5A, 5B, 5C, 5D, 5E, 72, 2B, 3F and 02, put back as
   read. Tunes to POC_HZ (wide) unless it is there already. Receive only. Returns the messages, or -3 (old image), -8 (scratch).
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 2
#endif
#ifndef K6_MODULE_OPT
#define K6_MODULE_OPT z
#endif
#include "../k6_mod.h"

#ifndef POC_HZ
#define POC_HZ 439987500u /* DAPNET, the amateur paging network, 1200 baud */
#endif
#if POC_HZ >= 430000000u && POC_HZ <= 450000000u
#define POC_TEXT 1
#endif
#ifndef WAIT_MS
#define WAIT_MS 0u
#endif
#ifndef FE_S
#define FE_S 900u        /* s a setting */
#endif
#if defined(POC_DUMP) && !defined(DUMP_CAPS)
#define DUMP_CAPS 6u     /* transmissions whose words are said */
#endif
#ifndef SLOT_SCALE
#define SLOT_SCALE 1000u /* ms a unit of FE_S (the emulated test shortens it) */
#endif

#define SYNC 0x7CD215D8u
#define IDLE 0x7A89C197u
#define GEN 0x769u       /* x^10 + x^9 + x^8 + x^6 + x^5 + x^3 + 1 */
#define CTRL_CLR 0x4000u
#define CTRL_EN 0x1000u
#define END_W 10u        /* words past its length, by time, a capture is over */
#define PRE_W 48u        /* words' worth of bits to find the first sync codeword in */
#define PRE_REP 28u      /* bits like the one before (the sync codeword has 15) before it */
#define LOST_B 1100u     /* bits without a sync codeword: the transmission is over */
#define TXTN 600u        /* bytes of messages held until the transmission ends */

/* a setting: REG_58, REG_2B's bits to set at every baud */
typedef struct {
  uint16_t cfg, af;
} var_t;

#if defined(POC_BENCH)
#define NV 3
static const var_t V[NV] = {
    {0x00E9, 0}, /* as built */
    {0x00E5, 0}, /* RX bandwidth bits 3:1 = 2 */
    {0x00E9, 0}, /* as built again */
};
#else
#define NV 1
static const var_t V[NV] = {{0x00E9, 0}};
#endif

/* the bauds the hunt goes round: REG_72 (10.32444 a baud; 1200 baud sampled at 4800), a word in
   1/16 ticks of 100 us, the hunt's look, REG_5D (the chip's packet: 2048 or 512 bytes), the
   chip's sync word, REG_2B's bits (de-emphasis off: run 1's best at 512 baud; the sampler's
   worst, run 2) */
#define NB 3
#define SMP 0 /* the sampled baud's place */
static const uint16_t B72[NB] = {49557, 24779, 5286};
static const uint16_t BAUD[NB] = {1200, 2400, 512};
static const uint16_t WT[NB] = {533, 1067, 5000};
static const uint8_t HUNT_MS[NB] = {60, 30, 90};
static const uint16_t LEN[NB] = {0xFFE0, 0xFFE0, 0xFF20};
static const uint16_t SYNW[NB] = {0xF0F0, 0xAAAA, 0xAAAA};
static const uint16_t AFB[NB] = {0, 0x0100, 0x0100};

static const uint8_t SAVED[] = {0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F};
#define NSV sizeof(SAVED)
#define SAVED_2B 8

enum { HUNT, BATCH, NEXT };

typedef struct {
  uint16_t sv[NSV];
  uint16_t syn[31];  /* the syndrome of each single wrong bit */
  uint16_t ring[8];  /* the words taken, by number & 7 */
  uint16_t alt[8];   /* the word it may have been instead (altm: the slots with one) */
  /* following the chip: when word rn is due and a word's time (units 100 us / 256), the last
     empty read's time, a pending read's, where the clock was last learnt; fl: 1 the last read
     was the empty FIFO, 2 a read is pending (fv, at pt), 4 the word taken last is checked by the
     next read (slot ck, taken cv, else cs) */
  uint32_t due, per, tp, pt, lmn, lmt;
  uint16_t fv, cv, cs;
  uint8_t altm, fl, ck;
  uint32_t sr;       /* the last 32 bits */
  uint32_t nb;       /* bits into a codeword, or since the hunt started */
  uint32_t el;       /* 100 us ticks since the start */
  uint16_t tl;
  uint32_t rn;       /* words taken this capture */
  uint8_t st, inv, ncw, cur, bi, np, bx;
  /* the sampler's slicer: the sample and its run so far, the run before (2: none), the chatter
     since, the carry */
  uint8_t sv1, pv, lb;
  int8_t carry;
  uint16_t sl, pl, ch;
  /* the transmission */
  uint32_t t0ms, w, q, ok, f, x, e, rpk, ncap, nm;
  /* the message */
  uint32_t ric, acc;
  uint8_t in, fn, na, taint, lost;
  /* the run */
  uint32_t ntx, nmsg, nfake, vtx, vmsg, vfake;
#ifdef POC_TEXT
  uint32_t th;       /* where the message being read starts in txt */
  uint32_t tn;
  uint8_t txt[TXTN];
#endif
  char ln[64];
#ifdef POC_DUMP
  /* the sampler's words of a transmission, for the host */
  uint16_t dn, nd;
  uint16_t dk[POC_DUMP];
#endif
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

static void say(const k6_api_t *api, st_t *s, char *end) {
  *end = 0;
  api->say(s->ln);
}

static uint32_t pop(uint32_t v) {
  uint32_t n = 0;
  while (v) v &= v - 1, n++;
  return n;
}

static uint32_t rem(uint32_t c) {
  int i;
  for (i = 30; i >= 10; i--)
    if (c >> i & 1) c ^= GEN << (i - 10);
  return c;
}

/* up to two wrong bits put right: how many, or -1 */
static int fix(const st_t *s, uint32_t *cw) {
  uint32_t r = rem(*cw >> 1), c;
  int i, j;
  if (!r) {
    if (pop(*cw) & 1) *cw ^= 1;
    else return 0;
    return 1;
  }
  for (i = 0; i < 31; i++)
    if (s->syn[i] == r) {
      c = *cw ^ (2u << i);
      if (pop(c) & 1) {
        *cw = c ^ 1;
        return 2;
      }
      *cw = c;
      return 1;
    }
  for (i = 0; i < 31; i++)
    for (j = i + 1; j < 31; j++)
      if ((s->syn[i] ^ s->syn[j]) == r) {
        c = *cw ^ (2u << i) ^ (2u << j);
        if (pop(c) & 1) return -1;
        *cw = c;
        return 2;
      }
  return -1;
}

#ifdef POC_TEXT
static void txt_put(st_t *s, uint32_t c) {
  if (s->tn < TXTN - 1) s->txt[s->tn++] = (uint8_t)c; /* the last byte for the end of a record */
}
#endif

/* the message being read ends */
static void msg_end(st_t *s) {
  if (!s->in) return;
  s->in = 0;
#ifdef POC_TEXT
  if (s->th < TXTN) {
    s->txt[s->th + 4] = s->lost;
    s->txt[s->tn++] = 0; /* txt_put leaves this byte free */
  }
#endif
}

/* a data bit of a message, in the order sent */
static void msg_bit(st_t *s, uint32_t b) {
  uint32_t n = s->fn ? 7u : 4u;
  s->acc |= b << s->na;
  if (++s->na < n) return;
#ifdef POC_TEXT
  {
    uint32_t c = s->acc;
    if (s->taint) c = '?';
    else if (!s->fn) c = (uint8_t)"0123456789*U -)("[c];
    else if (c == 0 || c == 3 || c == 4) c = 0; /* NUL, ETX, EOT: padding */
    else if (c < 32 || c > 126) c = '.';
    if (c) txt_put(s, c);
  }
#endif
  s->acc = 0, s->na = 0, s->taint = 0;
}

static void codeword(st_t *s, uint32_t cw) {
  uint32_t frame = s->ncw >> 1, i;
  int e = fix(s, &cw);
  if (e < 0) {
    s->x++, s->bx++;
    if (s->in) {
      s->lost++;
      for (i = 0; i < 20; i++) s->taint = 1, msg_bit(s, 0);
    }
    return;
  }
  if (e) s->f++, s->bx++; /* (a batch put right is a noisy one too) */
  else s->ok++;
  if (cw == IDLE) {
    msg_end(s);
  } else if (!(cw >> 31)) {
    msg_end(s);
    s->ric = (cw >> 13 & 0x3FFFFu) << 3 | frame;
    s->fn = (uint8_t)(cw >> 11 & 3);
    s->in = 1, s->acc = 0, s->na = 0, s->taint = 0, s->lost = 0;
    s->nm++;
#ifdef POC_TEXT
    if (s->tn + 8 > TXTN) {
      s->th = TXTN; /* no room: counted, not held */
    } else {
      s->th = s->tn;
      txt_put(s, s->ric >> 16), txt_put(s, s->ric >> 8), txt_put(s, s->ric), txt_put(s, s->fn);
      txt_put(s, 0);
    }
#endif
  } else if (s->in) {
    for (i = 0; i < 20; i++) msg_bit(s, cw >> (30 - i) & 1);
  }
}

/* one bit of the chip's; -> 1 when the transmission is over */
static int bit_in(st_t *s, uint32_t b) {
  b ^= s->inv;
  s->sr = s->sr << 1 | b;
  s->nb++;
  if (s->st == HUNT) {
    if (pop(s->sr ^ SYNC) > 2) {
      if (pop(~s->sr ^ SYNC) > 2) return s->nb > (s->q ? LOST_B : PRE_W * 16u);
      s->inv ^= 1; /* the stream is the other way up */
    }
    s->st = BATCH, s->nb = 0, s->ncw = 0, s->bx = 0, s->q++;
    return 0;
  }
  if (s->nb < 32) return 0;
  s->nb = 0;
  if (s->st == NEXT) {
    if (pop(s->sr ^ SYNC) <= 4) {
      s->st = BATCH, s->ncw = 0, s->bx = 0, s->q++;
    } else {
      /* no sync after a clean batch: the transmission is over (after a noisy one, four
         codewords or more put right or past help, a fade: the hunt for it goes on) */
      s->st = HUNT;
      msg_end(s);
      if (s->bx < 4) return 1;
    }
    return 0;
  }
  codeword(s, s->sr);
  if (++s->ncw == 16) s->st = NEXT;
  return 0;
}

/* the sampler: a run of l samples of v is (l + carry + 2) / 4 bits, at least one */
static int run_out(st_t *s, uint32_t v, uint32_t l) {
  int32_t q = (int32_t)l + s->carry, n = (q + 2) >> 2;
  int over = 0;
  s->carry = (int8_t)(l & 3 ? q - 4 * n : 0);
  if (n < 1) n = 1;
  while (n--) {
    if (v == s->lb && s->np < 255) s->np++; /* (before the first sync: the preamble alternates) */
    s->lb = (uint8_t)v;
    over |= bit_in(s, v);
  }
  return over;
}

/* a run of samples ends: one of a sample is chatter, held until the next long run, then shared
   out between it and the one before (or all of them one run, when those two are alike) */
static int run_in(st_t *s, uint32_t v, uint32_t l) {
  int over;
  if (s->pv > 1) {
    s->pv = (uint8_t)v, s->pl = (uint16_t)l;
    return 0;
  }
  if (l <= 1) {
    if (s->ch < 0x7FFF) s->ch++;
    return 0;
  }
  if (s->ch) {
    if (s->pv == v) {
      l += s->pl + s->ch, s->ch = 0;
      s->pl = (uint16_t)(l < 0x7FFF ? l : 0x7FFF);
      return 0;
    }
    s->pl += s->ch >> 1, l += s->ch - (s->ch >> 1), s->ch = 0;
  } else if (s->pv == v) {
    s->pl += (uint16_t)l;
    return 0;
  }
  over = run_out(s, s->pv, s->pl);
  s->pv = (uint8_t)v, s->pl = (uint16_t)(l < 0x7FFF ? l : 0x7FFF);
  return over;
}

static int smp_in(st_t *s, uint32_t b) {
  int over = 0;
  if (b == s->sv1) {
    if (s->sl < 0x7FFF) s->sl++;
    return 0;
  }
  if (s->sv1 < 2) over = run_in(s, s->sv1, s->sl);
  s->sv1 = (uint8_t)b, s->sl = 1;
  return over;
}

/* a word of a capture to the decoder; -> 1 when the transmission is over. Before the first sync
   codeword of a hunt the words are the preamble's (AAAA or 5555, a wrong bit or two): four others
   in a row and it was a false start (the sampler's: PRE_REP bits like the one before) */
static int word_in(st_t *s, uint32_t v) {
  uint32_t i;
  int over = 0;
#ifdef POC_DUMP
  if (s->bi == SMP && s->dn < POC_DUMP) s->dk[s->dn++] = (uint16_t)v;
#endif
  if (s->bi == SMP) {
    for (i = 16; i--;) over |= smp_in(s, v >> i & 1);
    return over || (!s->q && s->st == HUNT && s->np > PRE_REP);
  }
  if (!s->q && s->st == HUNT) {
    if (pop(v ^ 0xAAAAu) <= 2 || pop(v ^ 0x5555u) <= 2) s->np = 0;
    else if (++s->np > 3) return 1;
  }
  for (i = 16; i--;) over |= bit_in(s, v >> i & 1);
  return over;
}

/* the modem off and on with a two-byte sync word, armed twice (a new sync word alone does not
   take hold, and arming alone does not end a packet the chip is in); -> the FIFO's stale word
   (what an empty FIFO gives) */
static uint32_t seek(const k6_api_t *api, const st_t *s, uint32_t sync) {
  uint32_t i, w = 0, ctrl = 0x00F0u;
  api->fd_write(0x3F, 0);
  api->fd_write(0x59, 0);
  api->fd_write(0x58, 0);
  api->fd_write(0x2B, (uint16_t)(s->sv[SAVED_2B] | V[s->cur].af | AFB[s->bi]));
  api->fd_write(0x5A, (uint16_t)sync);
  api->fd_write(0x5B, (uint16_t)sync);
  api->fd_write(0x58, V[s->cur].cfg);
  api->fd_write(0x72, B72[s->bi]);
  api->fd_write(0x5C, 0x5625); /* no CRC of the chip's */
  api->fd_write(0x5D, LEN[s->bi]);
  api->fd_write(0x5E, 0x0206);
  for (i = 0; i < 2; i++) {
    api->fd_write(0x59, (uint16_t)(ctrl | CTRL_CLR));
    api->fd_write(0x59, (uint16_t)(ctrl | CTRL_EN));
    api->fd_write(0x02, 0);
    if (!i) api->delay_us(2000);
  }
  for (i = 0; i < 8; i++) w = api->fd_read(0x5F);
  return (w & 0xFF) << 8 | w >> 8;
}

static uint32_t swap(uint32_t w) { return (w & 0xFF) << 8 | w >> 8; }

/* the clock: 100 us ticks since the start, from the 16-bit tick counter (read often) */
static uint32_t clk(const k6_api_t *api, st_t *s) {
  uint16_t t = (uint16_t)api->ticks();
  s->el += (uint16_t)(t - s->tl);
  s->tl = t;
  return s->el;
}

/* word rn taken: the word eight before it (its slot's, which the empty reads since have
   checked) goes to the decoder. seen: it came between the last empty read and now (when), so
   the time it was due is put right, and over sixteen words or more the chip's clock */
static int take(st_t *s, uint32_t v, int seen, uint32_t when) {
  uint32_t k = s->rn & 7, n;
  int over = s->rn >= 8 ? word_in(s, s->ring[k]) : 0;
  s->ring[k] = (uint16_t)v, s->altm &= (uint8_t)~(1u << k), s->rn++, s->w++;
  if (seen) {
    int32_t e = (int32_t)(when - s->due);
    if (e > (int32_t)(s->per >> 2) || -e > (int32_t)(s->per >> 2)) s->due = when;
    else s->due += (uint32_t)(e / 2);
    n = s->rn - 1 - s->lmn;
    if (n >= 16) {
      if (n <= 64) s->per = (s->per + udiv(s->due - s->lmt, n)) >> 1;
      s->lmn = s->rn - 1, s->lmt = s->due;
    }
  }
  s->due += s->per;
  return over;
}

/* a read v of the FIFO at now; -> 1 when the transmission is over */
static int fol(st_t *s, uint32_t v, uint32_t now) {
  uint32_t k, w, q = s->per * 45 >> 7; /* 0.35 of a word */
  int over = 0;
  if (s->fl & 4) {
    /* a word came after the empty FIFO's: the same again and it may have been the empty
       FIFO's next slot, the word taken alike the one eight before */
    s->fl &= 3;
    if (v == s->cv) s->ring[s->ck] = s->cs, s->alt[s->ck] = s->cv, s->altm |= (uint8_t)(1u << s->ck);
  }
  if (s->fl & 2) {
    s->fl &= 5;
    if (v == s->fv) {
      /* the same again: the empty FIFO's slot holds not the word taken there but this */
      s->ring[s->rn & 7] = (uint16_t)v, s->altm &= (uint8_t)~(1u << (s->rn & 7)), s->e++;
      s->fl |= 1, s->tp = now;
      return 0;
    }
    over = take(s, s->fv, (s->fl & 1) && s->pt - s->tp < (s->per * 51 >> 8), s->tp + (s->pt - s->tp) / 2);
    s->fl &= 6;
    if (over) return over;
  }
  k = s->rn & 7;
  w = s->ring[k];
  if (s->altm >> k & 1) {
    if (v == w) s->altm &= (uint8_t)~(1u << k);
    else if (v == s->alt[k]) {
      /* the other way it may have been */
      s->ring[k] = (uint16_t)v, s->altm &= (uint8_t)~(1u << k), s->e++;
      s->fl |= 1, s->tp = now;
      return 0;
    }
  }
  if (v != w) {
    int st = s->fl & 1;
    if ((int32_t)(now - s->due + q) < 0) {
      /* too early for a word: the next read tells */
      s->fl |= 2, s->fv = (uint16_t)v, s->pt = now;
      return 0;
    }
    over = take(s, v, st && now - s->tp < (s->per * 51 >> 8), s->tp + (now - s->tp) / 2);
    s->fl &= 6;
    if (st && s->rn > 8) s->fl |= 4, s->ck = (uint8_t)k, s->cv = (uint16_t)v, s->cs = (uint16_t)w;
    return over;
  }
  if ((int32_t)(now - s->due - q) >= 0) {
    /* well past due: a word alike the one eight before came (the next slot's alike too) */
    s->fl &= 6;
    return take(s, v, 0, 0);
  }
  s->fl |= 1, s->tp = now;
  return 0;
}

static void tx_say(const k6_api_t *api, st_t *s) {
  char *p;
  p = kv(s->ln, "p ", ++s->ntx);
  p = kv(p, " s=", s->cur);
  p = kv(p, " b=", BAUD[s->bi]);
  p = kv(p, " t=", s->t0ms);
  p = kv(p, " w=", s->w);
  p = kv(p, " i=", s->inv);
  p = kv(p, " e=", s->e);
  p = kv(p, " r=-", (uint32_t)-KM_RSSI_DBM(s->rpk));
  say(api, s, p);
  p = kv(s->ln, "k ", s->ntx);
  p = kv(p, " q=", s->q);
  p = kv(p, " ok=", s->ok);
  p = kv(p, " f=", s->f);
  p = kv(p, " x=", s->x);
  p = kv(p, " c=", s->ncap);
  p = kv(p, " m=", s->nm);
  say(api, s, p);
#ifdef POC_TEXT
  {
    uint32_t i = 0;
    while (i + 5 <= s->tn) {
      uint32_t ric = (uint32_t)s->txt[i] << 16 | (uint32_t)s->txt[i + 1] << 8 | s->txt[i + 2];
      p = kv(s->ln, "m ", ric);
      p = kv(p, " ", s->txt[i + 3]);
      p = kv(p, " e", s->txt[i + 4]);
      *p++ = ' ';
      i += 5;
      while (i < s->tn && s->txt[i]) {
        if (p - s->ln >= 58) {
          say(api, s, p);
          p = put(s->ln, "+ ");
        }
        *p++ = (char)s->txt[i++];
      }
      i++;
      say(api, s, p);
    }
  }
#endif
#ifdef POC_DUMP
  /* the words as pocdump says them (python3 -m modem.pocsag dump LOG): a c line (e: words the
     empty FIFO put right), then d lines of twelve words */
  if (s->bi == SMP && s->dn && s->nd < DUMP_CAPS) {
    uint32_t i, k = 0;
    s->nd++;
    p = kv(s->ln, "c ", s->ntx);
    p = kv(p, " s=", s->cur);
    p = kv(p, " t=", s->t0ms);
    p = kv(p, " w=", s->dn);
    p = kv(p, " e=", s->e);
    p = hex4(put(p, " b="), B72[SMP]);
    p = kv(p, " m=4 i=", s->inv);
    say(api, s, p);
    for (i = 0; i < s->dn; i++) {
      if (!k) {
        p = kv(s->ln, "d ", s->ntx);
        *p++ = ' ';
        p = hex4(p, i);
        *p++ = ' ';
      }
      p = hex4(p, s->dk[i]);
      if (++k == 12) say(api, s, p), k = 0;
    }
    if (k) say(api, s, p);
  }
  s->dn = 0;
#endif
  s->vtx++, s->nmsg += s->nm, s->vmsg += s->nm;
}

/* the capture is over: the next batch's sync in the same transmission, or the transmission's
   lines; -> 1 to follow on */
static int cap_end(const k6_api_t *api, st_t *s, int over) {
  if (!over && s->q && s->st != HUNT) {
    /* the chip's packet ended inside a transmission: what the message being read had of this
       batch after it is lost */
    if (s->in) s->lost += 16;
    msg_end(s);
    return 1;
  }
  api->fd_write(0x59, 0); /* the chip's packet stopped: the FIFO is not filling while lines go */
  if (s->q) {
    msg_end(s);
    tx_say(api, s);
  } else {
    s->nfake++, s->vfake++;
  }
  return 0;
}

int k6_main(const k6_api_t *api) {
  st_t *s;
  uint32_t room, i, now, tkey = 0, tset, tarm = 0, prev = 0, t0 = 0, nw = 0;
  int key = 0, cap = 0, follow = 0, armed = 0, sline = 1, retune;
  char *p;
  if (api->level < 2) return -3;
  s = (st_t *)api->scratch(&room);
  if (room < sizeof(st_t)) return -8;
  for (i = 0; i < sizeof(st_t) / 4; i++) ((uint32_t *)s)[i] = 0;
  for (i = 0; i < NSV; i++) s->sv[i] = api->fd_read(SAVED[i]);
  for (i = 0; i < 31; i++) s->syn[i] = (uint16_t)rem(1u << i);
  /* tuned already (on POC_HZ, wide): no tune, whose RX restart sets the AFC going again, and
     the sampler's 1200 baud samples are noisy for a minute or two while it pulls in (bench run
     6; with the AFC off the sampler found nothing, run 7) */
  retune = api->rx_hz() != POC_HZ || api->fd_read(0x43) != 0x3028;
  if (retune) api->tune(POC_HZ, 1);
  s->tl = (uint16_t)api->ticks();
  p = kv(s->ln, "pocsag start hz=", POC_HZ);
  p = kv(p, " n=", NV);
  p = kv(p, " tune=", (uint32_t)retune);
#ifdef POC_TEXT
  p = put(p, " text=1");
#else
  p = put(p, " text=0");
#endif
  say(api, s, p);
  while (api->ms() < WAIT_MS && !key) {
    api->delay_us(50000);
    key = api->level >= 4 ? api->key_poll() : api->key_down();
  }
  tset = api->ms();
  while (!key) {
    now = api->ms();
    clk(api, s);
    if (now - tkey >= 50) {
      uint32_t r = api->fd_read(0x67) & 0x1FF;
      tkey = now;
      if (cap && r > s->rpk) s->rpk = r;
      key = api->level >= 4 ? api->key_poll() : api->key_down();
      if (key) break;
    }
    if (!cap && !follow && now - tset >= FE_S * SLOT_SCALE) { /* a setting's end */
      p = kv(s->ln, "v ", s->cur);
      p = kv(p, " n=", s->vtx);
      p = kv(p, " m=", s->vmsg);
      p = kv(p, " z=", s->vfake);
      say(api, s, p);
      s->vtx = s->vmsg = s->vfake = 0;
      if (++s->cur >= NV) break;
      tset = now, sline = 1, armed = 0;
    }
    if (sline) {
      sline = 0;
      p = kv(s->ln, "s ", s->cur);
      p = kv(p, " t=", now);
      p = hex4(put(p, " cfg="), V[s->cur].cfg);
      p = hex4(put(p, " a="), V[s->cur].af);
      say(api, s, p);
    }
    if (!cap) {
      uint32_t v;
      if (follow && now - tarm >= 2u * udiv(544000u, BAUD[s->bi]) + 50u) {
        follow = 0, armed = 0; /* no next batch: the transmission is over */
        tx_say(api, s);
      }
      if (!armed || (!follow && now - tarm >= HUNT_MS[s->bi])) {
        /* the hunt: the next baud with the preamble as the sync word */
        if (armed && ++s->bi >= NB) s->bi = 0;
        prev = seek(api, s, SYNW[s->bi]);
        armed = 1, tarm = api->ms();
      }
      v = swap(api->fd_read(0x5F));
      if (v != prev) {
        /* the chip's packet has begun: its first word */
        cap = 1, t0 = clk(api, s) << 4;
        s->np = 0, s->nb = 0, s->ncw = 0;
        s->sv1 = s->pv = s->lb = 2, s->ch = 0, s->carry = 0;
        nw = ((uint32_t)((LEN[s->bi] >> 5 & 7) << 8 | LEN[s->bi] >> 8) + 1) >> 1;
        if (!follow) {
          s->t0ms = now, s->w = 0, s->q = 0, s->ok = s->f = s->x = s->e = 0, s->rpk = 0;
          s->nm = 0, s->ncap = 0, s->in = 0;
          s->st = HUNT, s->inv = 0;
#ifdef POC_TEXT
          s->tn = 0;
#endif
#ifdef POC_DUMP
          s->dn = 0;
#endif
        } else if (s->bi == SMP) {
          s->st = HUNT; /* the sampler's packet began anywhere: the next sync codeword */
        } else {
          s->st = BATCH, s->q++;
        }
        s->bx = 0;
        s->ncap++, follow = 0;
        /* word 0 came just now (the slots after it hold what they held before: unknown) */
        for (i = 0; i < 8; i++) s->ring[i] = (uint16_t)prev;
        s->ring[0] = (uint16_t)v, s->rn = 1, s->w++, s->altm = 0, s->fl = 0;
        s->per = (uint32_t)WT[s->bi] << 4, s->lmn = 0, s->lmt = t0 << 4, s->due = s->lmt + s->per;
      }
    }
    /* reading: until the FIFO gives the empty FIFO's word (fol()) */
    while (cap) {
      int over = fol(s, swap(api->fd_read(0x5F)), clk(api, s) << 8);
      /* (the chip's packet is over by time, a sixteenth late) */
      if (over || s->rn >= nw ||
          (int32_t)((clk(api, s) << 4) - t0 - (nw + nw / 16 + END_W) * WT[s->bi]) >= 0) {
        /* the last eight words, unchecked, to the decoder */
        for (i = s->rn > 8 ? s->rn - 8 : 0; i < s->rn && !over; i++) over = word_in(s, s->ring[i & 7]);
        cap = 0, armed = 0;
        follow = cap_end(api, s, over);
        if (follow) {
          prev = seek(api, s, s->bi == SMP ? 0xF0F0u : s->inv ? 0xEA27u : 0x15D8u);
          armed = 1, tarm = api->ms();
        }
      } else if ((s->fl & 7) == 1) {
        break; /* the empty FIFO: on again after a pause */
      }
    }
    api->delay_us(300);
  }
  for (i = 0; i < NSV; i++) api->fd_write(SAVED[i], s->sv[i]);
  api->fd_write(0x02, 0);
  p = kv(s->ln, "pocsag end n=", s->ntx);
  p = kv(p, " m=", s->nmsg);
  p = kv(p, " z=", s->nfake);
  say(api, s, p);
  return (int)s->nmsg;
}
