/* rds.h: an RDS / RBDS group decoder for the radios' broadcast FM chips (docs/k6-fm-chip.md).

   The chip (the K6's RDA5807M; any RDA580x-style part) does the bit sync, the block sync and the
   error correction, and hands over each group as four 16-bit blocks A-D with an error level per
   block (BLER 0 clean, 1-2 corrected, 3 not correctable; RDA's verbose mode gives all four).
   This file turns groups into what a station says about itself:

     PI      programme identification (block A, or C' in a version B group)
     PTY     programme type, TP / TA / MS flags (block B)
     PS      the station's 8-character name (0A / 0B: two characters a group in D)
     RT      radio text, up to 64 characters (2A: four a group in C and D; 2B: 32, two in D);
             the A/B flag flipping means new text, so the old one is dropped
     ECC     the extended country code (1A, variant 0)
     CT      the clock (4A): Modified Julian Day, UTC hour and minute, local offset in half
             hours. A station sends it once a minute, the group starting within 0.1 s of the
             minute edge (IEC 62106), which is what makes it a time source

   Text, PTY, the flags and the ECC are taken only when the same value arrives twice in a row
   (for text, at that position; run fmr2 showed one weak station's single groups claiming PTY 30
   "Alarm Test" and TA), so one
   wrong correction can't put a wrong character in; with the error levels known (strict), a block
   must also be clean (BLER 0). A clock group must be clean in B, C and D when strict (a station
   at RSSI 62 sent two 4A groups with BLER 1-2 that read hour 26 and dates decades off: run
   fmp1b), and always pass the range checks below. Without error levels it is marked unchecked
   (cte 0xFF), so a caller can ask for agreement between stations before trusting it.

   No division, no libraries, no writable statics: it runs in the K6's and the UV-21's RAM
   modules as well as on a PC. SPDX-License-Identifier: MIT */
#ifndef RDS_H
#define RDS_H
#include <stdint.h>

#define RDS_TP 1u     /* traffic programme */
#define RDS_TA 2u     /* traffic announcement on now */
#define RDS_MS 4u     /* music (else speech) */
#define RDS_PI 8u     /* pi is from a clean block */

#define RDS_EV_CT 1u  /* rds_group: a new clock group passed the checks (ct fields) */
#define RDS_EV_PS 2u  /* the name just became complete */
#define RDS_EV_RT 4u  /* a radio text segment was confirmed */

#define RDS_CTE_UNCHECKED 0xFFu
#define RDS_MJD_LO 59945u  /* 2023-01-01: earlier dates are a garbled clock group */
#define RDS_MJD_HI 73050u  /* 2059-01-01 */

typedef struct {
  uint16_t pi, g, ok;       /* PI; groups; groups clean in all four blocks (strict only) */
  uint8_t pty, fl;          /* PTY 0-31; RDS_TP | RDS_TA | RDS_MS | RDS_PI (each seen twice in a row) */
  uint8_t ptyc, tamc, eccc; /* their candidates (+ 1; 0 none yet) */
  uint8_t psok, ecc;        /* the name's pairs confirmed (bits 0-3); ECC, 0 none heard */
  uint8_t rtab, rtend;      /* the text's A/B flag + 1, + 2 for 2B (0 none yet); its end ('\r') + 1 */
  uint16_t rtok;            /* the text's segments confirmed (bit n: characters 4n..4n+3) */
  uint32_t gt;              /* group types heard: bit 2 * type + version (B) */
  char ps[8];               /* the name ('\0' where not confirmed yet) */
  char rt[64];              /* the text */
  uint16_t psc[4];          /* the name's candidates: the last pair seen at each position */
  uint16_t rtc[32];         /* the text's candidates, two characters each */
  uint32_t mjd;             /* the last clock group that passed: MJD, */
  uint8_t hh, mm, cte;      /* UTC hour and minute, BLER B C D (2 bits each; RDS_CTE_UNCHECKED) */
  int8_t off;               /* local offset, half hours */
} rds_t;

static inline void rds_init(rds_t *s) {
  uint8_t *b = (uint8_t *)s;
  uint32_t i;
  for (i = 0; i < sizeof *s; i++) b[i] = 0;
}

/* the 2-bit error level of block n (0 A .. 3 D) in e = A << 6 | B << 4 | C << 2 | D */
#define RDS_BLER(e, n) (((e) >> (6 - 2 * (n))) & 3u)

/* one confirmed pair of text characters into dst at pos: candidates must match twice */
static inline int rds_pair(uint16_t *cand, char *dst, uint16_t w) {
  int same = *cand == w;
  *cand = w;
  if (!same) return 0;
  dst[0] = (char)(w >> 8), dst[1] = (char)(w & 0xFF);
  return 1;
}

/* a group: b[0..3] blocks A-D, e their error levels (A << 6 | B << 4 | C << 2 | D), strict 1
   when e is real (0 when the chip gives none: then e should be 0). Returns RDS_EV_ bits. */
static inline uint32_t rds_group(rds_t *s, const uint16_t *b, uint32_t e, int strict) {
  uint32_t ev = 0, ty, ver, seg, i;
  uint16_t B = b[1];
  if (s->g < 0xFFFFu) s->g++;
  if (strict && !e && s->ok < 0xFFFFu) s->ok++;
  if (!RDS_BLER(e, 0)) s->pi = b[0], s->fl |= RDS_PI;
  if (strict && RDS_BLER(e, 1)) return 0; /* a type we can't trust: nothing else to read */
  ty = B >> 12, ver = (B >> 11) & 1u;
  s->gt |= 1u << (ty * 2 + ver);
  { /* PTY and TP: taken when two groups in a row agree, as the text is (one bad B can't set them) */
    uint8_t v = (uint8_t)(((B >> 5) & 63u) + 1u); /* TP << 5 | PTY, + 1 */
    if (s->ptyc == v) {
      s->pty = (uint8_t)((v - 1u) & 31u);
      s->fl = (uint8_t)((s->fl & ~RDS_TP) | ((v - 1u) & 32u ? RDS_TP : 0));
    }
    s->ptyc = v;
  }
  if (ver && !RDS_BLER(e, 2) && !(s->fl & RDS_PI)) s->pi = b[2];
  if (ty == 0) { /* 0A / 0B: TA, MS, the name */
    uint8_t tm = (uint8_t)(((B >> 3) & 3u) + 1u); /* TA << 1 | MS, + 1 */
    if (s->tamc == tm)
      s->fl = (uint8_t)((s->fl & ~(RDS_TA | RDS_MS)) | ((tm - 1u) & 2u ? RDS_TA : 0) | ((tm - 1u) & 1u ? RDS_MS : 0));
    s->tamc = tm;
    seg = B & 3u;
    if ((!strict || !RDS_BLER(e, 3)) && rds_pair(&s->psc[seg], &s->ps[2 * seg], b[3])) {
      uint32_t was = s->psok;
      s->psok |= (uint8_t)(1u << seg);
      if (s->psok == 15 && was != 15) ev |= RDS_EV_PS;
    }
  } else if (ty == 1 && !ver) { /* 1A: variant 0 carries the ECC */
    if ((!strict || !RDS_BLER(e, 2)) && !(b[2] & 0x7000u)) {
      uint8_t v = (uint8_t)(b[2] & 0xFF);
      if (s->eccc == v + 1u) s->ecc = v; /* (0xFF never repeats as + 1: fine, no ECC is 0xFF) */
      s->eccc = (uint8_t)(v + 1u);
    }
  } else if (ty == 2) { /* 2A / 2B: radio text */
    uint32_t ab = ((B >> 4) & 1u) + 1u + (ver ? 2u : 0); /* a new flag or version: new text */
    if (s->rtab != ab) {
      if (s->rtab) {
        for (i = 0; i < 64; i++) s->rt[i] = 0;
        for (i = 0; i < 32; i++) s->rtc[i] = 0;
        s->rtok = 0, s->rtend = 0;
      }
      s->rtab = (uint8_t)ab;
    }
    seg = B & 15u;
    if (!ver) { /* four characters: C then D; the segment counts once both pairs are in */
      int c = (!strict || !RDS_BLER(e, 2)) && rds_pair(&s->rtc[2 * seg], &s->rt[4 * seg], b[2]);
      int d = (!strict || !RDS_BLER(e, 3)) && rds_pair(&s->rtc[2 * seg + 1], &s->rt[4 * seg + 2], b[3]);
      if (c && d) s->rtok |= (uint16_t)(1u << seg), ev |= RDS_EV_RT;
    } else if ((!strict || !RDS_BLER(e, 3)) && rds_pair(&s->rtc[seg], &s->rt[2 * seg], b[3])) {
      s->rtok |= (uint16_t)(1u << seg), ev |= RDS_EV_RT;
    }
    if (ev & RDS_EV_RT)
      for (i = 0; i < 64; i++)
        if (s->rt[i] == '\r') {
          if (!s->rtend || i + 1 < s->rtend) s->rtend = (uint8_t)(i + 1);
          break;
        }
  } else if (ty == 4 && !ver) { /* 4A: the clock */
    uint32_t mjd = (uint32_t)(B & 3u) << 15 | (uint32_t)b[2] >> 1;
    uint32_t hh = (uint32_t)(b[2] & 1u) << 4 | (uint32_t)b[3] >> 12, mm = ((uint32_t)b[3] >> 6) & 63u;
    uint32_t off = b[3] & 31u, cte = e & 0x3Fu;
    if ((!strict || !cte) && hh < 24 && mm < 60 && off <= 28 && mjd >= RDS_MJD_LO && mjd < RDS_MJD_HI) {
      s->mjd = mjd, s->hh = (uint8_t)hh, s->mm = (uint8_t)mm;
      s->off = (int8_t)(b[3] & 32u ? -(int)off : (int)off);
      s->cte = (uint8_t)(strict ? cte : RDS_CTE_UNCHECKED);
      ev |= RDS_EV_CT;
    }
  }
  return ev;
}

/* MJD to the calendar date (2000-2099), by subtraction: *y in full, *m 1-12, *d 1-31 */
static inline void rds_date(uint32_t mjd, uint32_t *y, uint32_t *m, uint32_t *d) {
  static const uint8_t ml[12] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  uint32_t n = mjd - 51544u, yy = 2000, mo = 0, len; /* MJD 51544 = 2000-01-01 */
  for (;;) {
    len = (yy & 3u) ? 365u : 366u;
    if (n < len) break;
    n -= len, yy++;
  }
  for (;;) {
    len = ml[mo] + (mo == 1 && !(yy & 3u));
    if (n < len) break;
    n -= len, mo++;
  }
  *y = yy, *m = mo + 1, *d = n + 1;
}

#endif
