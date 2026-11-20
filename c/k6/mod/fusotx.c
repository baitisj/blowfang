/* fusotx: FuSotto from the K6. It sends what FM SOTTO (fmsotto.c) recorded into the outbox (API
   level 15, outbox.h) as one sotto VW call over YSF: header, VW frames FN 0-6 of five sotto 1200
   superframes each, terminator, from AG7EW to ALL (as the UV-380 and the sotto softmodem send it,
   modem/ysf.py tx_sotto), on c4lock's chip-locked C4FM (docs/k6-c4fm.md), then AG7EW in MCW.

   First it makes the frames: each one with c/common/ysf_tx.c, its signs and sizes into the
   outbox after the recording (OB_PL), four frames (one chip packet) a group. Then c4lock's
   calibration and one chained call, a group read from SPI in the 48 symbols before each packet's
   lead. Two things c4ftm and c4voice didn't need:
     - the splices: each chained packet's lead goes over the last 48 symbols of the frame before
       it. In a VW frame those are coded speech, so the lead goes out at the inner size: weak
       wrong guesses that the sotto VW code corrects (offline, 99% of those frames' superframes
       survive; with the lead at the outer size, 59%);
     - the length: a call runs up to 40 s (80 s of speech: 2,010 superframes; a longer recording
       is cut there), so each chained packet's TX done steers the grid (c4lock.c cont()).
   Armed after the upload as c4ftm: a K6 key press, or a DTMF '1' on 440.850, is the go; it keys
   5 s later with the channel clear. PA off unless built with -DC4_PA=1. A Pluto capture goes to
   python3 tools/k6_fusotx.py air CAP.wav (the superframes it decodes, against the outbox's).

   It says, besides c4lock's lines,
     K6 MOD say fusotx frames=404 sf=2010 of 2250 ms=...   the frames made (and their time)
     K6 MOD say fusotx sf=... frames=... trk=... dmax=...% big=... slack=... us
                                    superframes and frames sent, chained packets that steered the
                                    grid, the worst TX done off its tick, ones over a quarter
                                    symbol, the least time to spare before a chained enable
   and returns the packets sent, or c4lock's errors, -1 a K6 under API level 15, or -12 no
   recording (FUSO_SLOT: no frames in that library slot).

   FUSO_SLOT=n (a bench build for a K6 without level 15): the frames come ready made from library
   slot n (tools/k6_fusotx.py deck, installed with --cards --install n), nothing is written.

   Build: python -m modem.k6 mod out.wav c/k6/mod/fusotx.c --key-file KEY.
   SPDX-License-Identifier: MIT */
#ifndef K6_MODULE_LEVEL
#define K6_MODULE_LEVEL 15  /* the outbox (and its 15,408-byte block: the code is over 11,776) */
#endif
#define K6_MODULE_OPT z     /* as c4lock.c: -Oz */
#define C4_DEMO 1
#define C4_CHAIN 1
#define C4_FUSO 1
#ifndef C4_INNER
#define C4_INNER 20         /* c4voice's best inner size (11-18 voice3) */
#endif
#ifndef C4_WAIT_MS
#define C4_WAIT_MS 2400000  /* armed up to 40 minutes */
#endif
#ifndef C4_KEYGO
#define C4_KEYGO 1          /* armed after the upload: a K6 key press or DTMF '1' is the go */
#endif
#ifndef K6_NAME
#define K6_NAME "FUSOTTO TX"
#endif
#define K6_TITLE "FUSOTTO 440.850"
#include "../k6_mod.h"
#include "m0rt.h"          /* ysf_tx.c divides */
#include "outbox.h"
#include "../../common/ysf_tx.c"
#include "c4lock.c"

#define LIB_BASE 0x110000u  /* the SPI module library: 32 slots of 16 KB (k6_lib.h) */
#define LIB_SLOT 0x4000u

#if !FUSO_SLOT
/* n bytes into SPI at a, a page at a time: 0 or -1 */
static int prog(const k6_api_t *api, uint32_t a, const uint8_t *b, uint32_t n) {
  while (n) {
    uint32_t k = 256u - (a & 255u);
    if (k > n) k = n;
    if (api->fl_prog(a, b, k)) return -1;
    a += k, b += k, n -= k;
  }
  return 0;
}

#endif

/* the byte sum of n bytes in SPI from a */
static uint32_t spi_sum(const k6_api_t *api, uint32_t a, uint32_t n, uint8_t *buf, uint32_t len) {
  uint32_t sum = 0, i;
  while (n) {
    uint32_t k = n < len ? n : len;
    api->fl_read(a, buf, k);
    for (i = 0; i < k; i++) sum += buf[i];
    a += k, n -= k;
  }
  return sum;
}

#if !FUSO_SLOT
/* frame k of the call (0 header, nf - 1 terminator, else VW frame k - 1) into fr */
static void frame(const k6_api_t *api, uint32_t k, uint32_t nf, uint32_t nsf, uint8_t *fr) {
  ysft_fich f;
  ysft_fich_default(&f);
  f.sotto = 1;
  if (!k || k == nf - 1u) {
    f.fi = k ? YSFT_FI_TERMINATOR : YSFT_FI_HEADER;
    ysft_frame_header(&f, "ALL", ID_TEXT, fr);
  } else {
    uint8_t sf[YSFT_SOTTO_NF * YSFT_SOTTO_SF];
    uint32_t j, n;
    for (j = 0; j < YSFT_SOTTO_NF; j++) {   /* the last superframe repeats to fill the last frame */
      n = YSFT_SOTTO_NF * (k - 1u) + j;
      api->fl_read(KM_OB_LO + OB_DATA + OB_SF * (n < nsf ? n : nsf - 1u), sf + OB_SF * j, OB_SF);
    }
    f.fi = YSFT_FI_COMMS;
    f.fn = (uint8_t)((k - 1u) % 7u);
    ysft_frame_sotto(&f, sf, fr);
  }
}

/* a frame's 480 dibits (MSB first) into signs (1 = +deviation: the dibit's MSB clear) and sizes
   (1 = outer: its LSB), as modem/k6c4fm.py split */
static void split(const uint8_t *fr, uint8_t *sg, uint8_t *sz) {
  uint32_t i, b;
  for (i = 0; i < 60u; i++) {
    uint32_t w = (uint32_t)fr[2u * i] << 8 | fr[2u * i + 1u], g = 0, z = 0;
    for (b = 0; b < 8u; b++, w <<= 2) {
      g = g << 1 | (~w >> 15 & 1u);
      z = z << 1 | (w >> 14 & 1u);
    }
    sg[i] = (uint8_t)g;
    sz[i] = (uint8_t)z;
  }
}
#endif

static int fuso_prep(c4_t *s, ui_t *ku) {
  const k6_api_t *api = s->api;
  uint32_t h[4], nf, nsf, ng;
  char line[64], *p;
#if FUSO_SLOT
  s->pl = LIB_BASE + FUSO_SLOT * LIB_SLOT + 16u;
  api->fl_read(s->pl, h, 16);
  nf = h[1], nsf = h[2], ng = (nf + OB_GFRAMES - 1u) / OB_GFRAMES;
  if (h[0] != OP_MAGIC || nf < 3u || OB_GRP + ng * OB_GBYTES > LIB_SLOT - 24u
      || spi_sum(api, s->pl + OB_GRP, ng * OB_GBYTES, s->fpl, sizeof s->fpl) != h[3])
    return -12;
  (void)ku;
  (void)line;
  (void)p;
#else
  uint32_t k, sum = 0, nrec, t0 = api->ms();
  uint8_t fr[YSFT_FRAME];
  api->fl_read(KM_OB_LO, h, 16);
  nrec = h[2];
  if (h[0] != OB_MAGIC || h[1] != (1u | KM_SOTTO_1200 << 8) || !nrec || nrec > OB_REC_CAP / OB_SF
      || spi_sum(api, KM_OB_LO + OB_DATA, nrec * OB_SF, s->fpl, sizeof s->fpl) != h[3])
    return -12;
  nsf = nrec < OB_SF_MAX ? nrec : OB_SF_MAX;
  nf = 2u + (nsf + YSFT_SOTTO_NF - 1u) / YSFT_SOTTO_NF;
  ng = (nf + OB_GFRAMES - 1u) / OB_GFRAMES;
  ui_text(ku, 5, "MAKING FRAMES");
  ui_now(ku);
  s->pl = OB_PL;
  for (k = 0; k < (OB_GRP + ng * OB_GBYTES + 4095u) / 4096u; k++)
    if (api->fl_erase(OB_PL + 4096u * k)) return -12;
  for (k = 0; k < ng * OB_GFRAMES; k++) {
    uint32_t j = k % OB_GFRAMES, i;
    uint8_t *sg = s->fpl + 60u * j, *sz = sg + OB_GFRAMES * 60u;
    if (k < nf) {
      frame(api, k, nf, nsf, fr);
      split(fr, sg, sz);
    } else {
      for (i = 0; i < 60u; i++) sg[i] = sz[i] = 0;
    }
    if (j == OB_GFRAMES - 1u) {
      for (i = 0; i < sizeof s->fpl; i++) sum += s->fpl[i];
      if (prog(api, OB_PL + OB_GRP + (k / OB_GFRAMES) * OB_GBYTES, s->fpl, sizeof s->fpl)) return -12;
    }
  }
  h[0] = OP_MAGIC, h[1] = nf, h[2] = nsf, h[3] = sum;
  if (prog(api, OB_PL, (const uint8_t *)h, 16)) return -12;
  p = put(line, "fusotx frames=");
  p = dec(p, nf);
  p = put(p, " sf=");
  p = dec(p, nsf);
  p = put(p, " of ");
  p = dec(p, nrec);
  p = put(p, " ms=");
  p = dec(p, api->ms() - t0);
  *p = 0;
  api->say(line);
#endif
  s->nf = nf;
  s->nsf = nsf;
  return 0;
}
