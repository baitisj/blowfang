/* fmsotto: what the K6 hears on FM, coded live as sotto 1200 (2026-11-20, Jeff: "receive FM
   audio and then encode"; docs/k6-fm-sotto.md).

   The K6 has no mic path into the MCU (docs/k6-mic-probe.md), so the voice comes in by radio:
   the FD6818's low IF on PB1 (GP0 code 13, gp0rx.h), read by DMA channel 1 flat out into a
   512 B ring, and demodulated in the DMA's half and full interrupts (dmairq.c showed the SRAM
   vector table takes them, with no half lost beside say and fl_read):

     counts (2 * ones - 64 per 64 DMA samples, ~87.8 kHz)
       -> mixed down by the nominal IF (26 MHz / 1536), a 2nd-order CIC by 6 (~14.6 kHz)
       -> phase by CORDIC; the phase step is the FM discriminator (aprsclk.c's front end)
       -> DC removed, 750 us de-emphasis (the inverse of pluto_bench --preemph: 0 dB at 1 kHz),
          a 3.5 kHz Butterworth low-pass
       -> linear interpolation to 8 kHz on TIM17's clock (the DMA's rate moves with the CPU's
          load, so the mixer and the interpolator follow the measured time of each half)
       -> a ring of 8 kHz samples.
   The foreground takes them 80 at a time into the slim sotto 1200 encoder (sotto
   c/fixed/sotto_fx_slim.c, the shape codebook read from SPI flash at 0x100000) and says the
   superframes as hex, four to a line. tools/k6_fmsotto.py turns a log back into a .bit file.

   Lines (under 60 characters):
     fms start ks=5621 spi=1
     s 0 0123456789ab...                     (superframes 0-3: 4 x 6 bytes, hex)
     fms 1 sps=8000 pk=13021 skip=0 miss=0 isr=31 rd=15      (each second)
     fms end n=750 lost=0 miss=0 h=440000 ob=700     (ob: superframes kept in the outbox)
   sps: 8 kHz samples made in the second; pk: their largest |value|; skip: 10 ms blocks the
   encoder fell behind on and skipped in the second (lost: in all); miss: DMA halves lost; isr:
   the interrupt's share of the CPU, %; rd: the SPI reads' share; spi=1: the reads go to SPI1
   directly (0: through the core's fl_read).
   Needs the cell deck in a library slot (tools/k6_fmsotto.py deck, installed with --cards).
   Runs RUN_MS or until a key. Receive only. Writes REG_34[3:0], REG_33 bit 8 (GP0), REG_7E and
   REG_13 (the gain frozen), SYSCFG CFGR1 and FLASH_ACR, the vector copy's entries 25 and 46,
   NVIC bit 9, TIM17, DMA channel 1 and their clock enables, all put back. Returns the
   superframes coded, or -2 (a key before any), -3 (old image), -4 (PB1 not an input), -5 (REG_33
   refused), -7 (TIM17 or DMA channel 1 in use), -8 (scratch), -9 (the vector copy isn't the
   bootloader's table), -10 (no cell deck in the library), -11 (the DMA never started).

   -DFMS_HOST builds the same front end and encoder for a PC (tools/k6_fmsotto.py sim): counts in,
   superframes out.
   SPDX-License-Identifier: MIT */
#ifndef FMS_HOST
#define K6_MODULE_LEVEL 16
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "m0rt.h"
K6_MODULE_NAME("FM SOTTO");
#else
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#endif
#include "outbox.h"

#ifndef RUN_MS
#define RUN_MS 90000u
#endif
#ifndef FMS_WIDE
#define FMS_WIDE 1
#endif
#ifndef GAIN13
#define GAIN13 0x03FFu /* REG_13 with the AGC frozen at its maximum (aprsclk.c) */
#endif
#define RSZ 512u       /* the DMA ring: halves of 256 B (46 us) */
#define HALF (RSZ / 2u)
#define DEC 6          /* counts an IF sample */
#define PCM_N 320      /* 8 kHz samples kept: four encoder blocks */
#define BLK 80
#define OUT_T 192000   /* 96 MHz cycles an 8 kHz sample, Q4 */
#define CAV0 (4372u << 4) /* cycles a half at 5.621 M transfers a second, Q4 */
#define DPH_K 11833u   /* the mixer's step per (cycles a half, Q4): 2^32 FIF 64 / (96e6 HALF 16) */
/* 750 us de-emphasis, zero at 3 kHz, 0 dB at 1 kHz, at 14.64 kHz (Q14) */
#define DE_B0 8439
#define DE_B1 (-1828)
#define DE_A1 14957
/* Butterworth low-pass, 3.5 kHz at 14.64 kHz (Q14) */
#define LP_B0 4475
#define LP_B1 8950
#define LP_A1 (-1314)
#define LP_A2 2830
#ifndef FMS_SQ_DBM
#define FMS_SQ_DBM (-100) /* the outbox records while the carrier is over this */
#endif
#define OB_CAP OB_REC_CAP /* the outbox (outbox.h): its header page, then superframes up to the frames */
#define OB_SECTORS ((RUN_MS / 1000u * 150u + OB_DATA + 4095u) / 4096u)
#define MAX_IRQ 40000u /* halves with no word from the foreground: the handler stops itself */
#define MAX_HALVES ((RUN_MS / 1000u + 30u) * 25000u) /* and after this many in all, whatever */

#define GPIOB 0x48000400u
#define RCC_AHBENR 0x40021014u
#define RCC_APB2ENR 0x40021018u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define DMA 0x40020000u
#define DMA_ISR DMA
#define DMA_IFCR (DMA + 0x04)
#define DMA_CCR1 (DMA + 0x08)
#define DMA_CNDTR1 (DMA + 0x0C)
#define DMA_CPAR1 (DMA + 0x10)
#define DMA_CMAR1 (DMA + 0x14)
#define SYSCFG_CFGR1 0x40010000u
#define FLASH_ACR 0x40022000u
#define NVIC_ISER 0xE000E100u
#define NVIC_ICER 0xE000E180u
#define NVIC_ICPR 0xE000E280u
#define SRAM0 0x20000000u
#define BOOT_VEC 0x08000000u
#define V_HF 3u
#define V_DMA1 (16u + 9u)
#define V_CTX 46u
#define IRQ_DMA1 9u
#define TIM17EN (1u << 18)
#define SYSCFGEN 1u
#define CB_ADDR 0x100020u /* the shape codebook in SPI flash, after its 32-byte header */

static const int8_t SIN[64] = {0,   12,  25,  37,  49,  60,  71,  81,  90,  98,  106, 112, 117,
                               122, 125, 126, 127, 126, 125, 122, 117, 112, 106, 98,  90,  81,
                               71,  60,  49,  37,  25,  12,  0,   -12, -25, -37, -49, -60, -71,
                               -81, -90, -98, -106, -112, -117, -122, -125, -126, -127, -126, -125,
                               -122, -117, -112, -106, -98, -90, -81, -71, -60, -49, -37, -25, -12};
static const uint16_t ATN[11] = {8192, 4836, 2555, 1297, 651, 326, 163, 81, 41, 20, 10};

typedef struct {
  /* mixer and CIC (unsigned: the integrators wrap) */
  uint32_t ph, dph, i1, i2, q1, q2, ci, cq, di, dq;
  int32_t k;
  /* discriminator and filters */
  int32_t ang, dc, dx1, dy1, s1, s2;
  /* the interpolator: the last IF-rate sample, Q4 cycles from it to the next 8 kHz instant,
     2^31 / the cycles an IF sample spans (the foreground's division) */
  int32_t yp, nxt, inv;
  /* time: TIM17 at the last half, the cycles a half (Q4, smoothed) */
  uint32_t c17, cav;
  /* the 8 kHz ring */
  uint32_t wi;
  volatile uint32_t pw, pk, halves, miss, quiet, isr;
  uint32_t deck; /* the cell deck's address in SPI flash */
  uint32_t rdc, spi; /* TIM17 cycles in SPI reads (foreground); 1: read SPI1 directly, 2: at PCLK / 2 too */
  uint32_t rec, obw, obs; /* the outbox: 0 waiting for a carrier, 1 recording, 2 full; bytes, their sum */
#ifndef FMS_HOST
  const k6_api_t *api;
  uint8_t *ring;
#endif
  int16_t pcm[PCM_N];
} fe_t;

static int32_t sat16(int32_t v) { return v > 32767 ? 32767 : v < -32767 ? -32767 : v; }

/* one IF sample (~14.6 kHz) through the discriminator and filters into the 8 kHz ring */
static void fe_audio(fe_t *f, int32_t x, int32_t y) {
  int32_t ang = 0, d, t, i, span;
  if (x < 0) x = -x, y = -y, ang = 32768;
  for (i = 0; i < 11; i++) {
    t = x;
    if (y > 0) x += y >> i, y -= t >> i, ang += ATN[i];
    else x -= y >> i, y += t >> i, ang -= ATN[i];
  }
  d = (int16_t)(uint16_t)(ang - f->ang); /* the phase step: 4.48 a Hz */
  f->ang = ang;
  d -= f->dc >> 8;
  f->dc += d;
  d >>= 2; /* +-8192: the filters' sums stay inside 32 bits (de-emphasis gains 13 dB at DC) */
  t = (DE_B0 * d + DE_B1 * f->dx1 + DE_A1 * f->dy1) >> 14;
  f->dx1 = d, f->dy1 = t;
  d = (LP_B0 * t + f->s1) >> 14;
  f->s1 = LP_B1 * t - LP_A1 * d + f->s2;
  f->s2 = LP_B0 * t - LP_A2 * d;
  d = sat16(d * 4); /* 3 kHz deviation at 1 kHz: 13,440 */
  span = (int32_t)((f->cav * 3u) >> 1); /* Q4 cycles an IF sample: 6 counts, 1.5 halves */
  while (f->nxt <= span) {
    int32_t fr = (int32_t)(((uint32_t)f->nxt * (uint32_t)f->inv) >> 17); /* Q14 */
    int32_t o = f->yp + (((d - f->yp) * fr) >> 14);
    uint32_t a = (uint32_t)(o < 0 ? -o : o);
    f->pcm[f->wi] = (int16_t)o;
    if (++f->wi == PCM_N) f->wi = 0;
    f->pw++;
    if (a > f->pk) f->pk = a;
    f->nxt += OUT_T;
  }
  f->nxt -= span;
  f->yp = d;
}

/* one count (2 * ones - 64) through the mixer and CIC */
static void fe_count(fe_t *f, int c) {
  uint32_t ix = f->ph >> 26;
  f->ph += f->dph;
  f->i1 += (uint32_t)(c * SIN[(ix + 16) & 63]), f->i2 += f->i1;
  f->q1 -= (uint32_t)(c * SIN[ix]), f->q2 += f->q1;
  if (++f->k == DEC) {
    uint32_t a = f->i2 - f->ci, b = f->q2 - f->cq;
    int32_t x = (int32_t)(a - f->di), y = (int32_t)(b - f->dq);
    f->ci = f->i2, f->cq = f->q2, f->di = a, f->dq = b, f->k = 0;
    fe_audio(f, x >> 4, y >> 4);
  }
}

/* a half's time (TIM17 cycles since the last): the smoothed rate and the mixer's step */
static void fe_rate(fe_t *f, uint32_t dt) {
  uint32_t q = dt << 4;
  if (q < 2u * f->cav && 2u * q > f->cav) f->cav = (uint32_t)((int32_t)f->cav + (((int32_t)q - (int32_t)f->cav) >> 4));
  f->dph = f->cav * DPH_K;
}

static void fe_init(fe_t *f) {
  memset(f, 0, sizeof *f);
  f->cav = CAV0;
  f->dph = CAV0 * DPH_K;
  f->inv = (int32_t)(0x80000000u / ((CAV0 * 3u) >> 1));
}

/* the next 10 ms block into x, or 0 when there isn't one yet; *lost counts blocks skipped
   because the encoder fell behind the interrupt */
static int fe_take(fe_t *f, uint32_t *pr, uint32_t *ri, uint32_t *lost, int16_t *x) {
  uint32_t pw = f->pw, i;
  if (pw - *pr < BLK) return 0;
  while (pw - *pr > PCM_N - BLK / 2) { /* the writer is near this block: skip it */
    *pr += BLK, (*lost)++;
    if ((*ri += BLK) == PCM_N) *ri = 0;
  }
  for (i = 0; i < BLK; i++) x[i] = f->pcm[*ri + i];
  *pr += BLK;
  if ((*ri += BLK) == PCM_N) *ri = 0;
  f->inv = (int32_t)(0x80000000u / ((f->cav * 3u) >> 1));
  return 1;
}

/* ---- the encoder: sotto's slim 1200, its read-only data in SPI flash ----
   The shape codebook (sotto's, at 0x100000, 20 bytes a vector) and the codebook cells (a deck
   in the SPI module library, tools/k6_fmsotto.py deck) are read as the search needs them, about
   200 reads a superframe, and the DCT's rows come from its 21 magnitudes: 3.7 KB of tables that
   don't fit beside the front end's buffers in RAM. -DFMS_HOST -DFMS_SPI builds the same reads
   against a file (the host test: the same superframes as sotto's own tables). */
#ifndef FMS_HOST
static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }
#endif
#if !defined(FMS_HOST) || defined(FMS_SPI)
#define FMS_FLASH 1
#ifdef FMS_HOST
static fe_t *g_fe;
static int host_fl_read(uint32_t a, void *buf, uint32_t n);
#define CTX() g_fe
#define FL_READF(f, a, b, n) ((void)(f), host_fl_read(a, b, n))
#else
#define CTX() ((const fe_t *)rd(SRAM0 + 4u * V_CTX))
/* SPI flash reads. Aura's fl_read runs from main flash, and with SRAM mapped at 0 (MEM_MODE 3)
   the flash prefetch is off (docs/k6-modules.md, txdog), so it runs slowly: on the radio
   (fmso1a) the encoder kept up with only 80 % of real time. So the encoder's reads go to SPI1
   directly from RAM, as stockfont.c does (READ 0x03 only; SPI1 as Aura left it, soft NSS on
   PA2), a byte at a time with bounded waits, interrupts on (no other SPI1 user runs while the
   module does; the DMA handler never reads the flash). The time in reads is counted on TIM17. */
#define SPI1 0x40013000u
#define SPI_CR1 (SPI1 + 0x00u)
#define SPI_SR (SPI1 + 0x08u)
#define SPI_DR (SPI1 + 0x0Cu)
#define GPIOA 0x48000000u
#define CS_PA2 (1u << 2)
#define SPIN 20000u
#define R8(a) (*(volatile uint8_t *)(uintptr_t)(a))
#define R16(a) (*(volatile uint16_t *)(uintptr_t)(a))
static int spi_rd(uint32_t a, uint8_t *b, uint32_t n) {
  /* READ, 3 address bytes, n bytes in. Up to 3 bytes in flight: the SPI's 4-byte receive FIFO
     can't overrun (Aura set it up for byte reads, FRXTH), and the master stops clocking when
     nothing is queued, so the DMA handler can interrupt anywhere. fmso1e, a byte at a time:
     27 % of the CPU in reads, about 1.9 us a byte */
  uint32_t tx = 0, rx = 0, k = 0, end = n + 4u;
  while (R16(SPI_SR) & 0x80u)
    if (++k > SPIN) return -1;
  for (k = 0; k < 8 && (R16(SPI_SR) & 1u); k++) (void)R8(SPI_DR);
  wr(GPIOA + 0x28u, CS_PA2);
  for (k = 0; rx < end;) {
    uint32_t sr = R16(SPI_SR);
    if (tx < end && tx - rx < 3u && (sr & 2u)) {
      R8(SPI_DR) = tx == 0 ? 0x03 : tx < 4u ? (uint8_t)(a >> (8u * (3u - tx))) : 0;
      tx++;
    }
    if (sr & 1u) {
      uint8_t d = R8(SPI_DR);
      if (rx >= 4u) b[rx - 4u] = d;
      rx++, k = 0;
    } else if (++k > SPIN) break;
  }
  for (k = 0; (R16(SPI_SR) & 0x80u) && k < SPIN; k++) {
  }
  wr(GPIOA + 0x18u, CS_PA2);
  return rx < end ? -1 : 0;
}
static int fl_rd(const fe_t *cf, uint32_t a, void *b, uint32_t n) {
  fe_t *f = (fe_t *)cf;
  uint32_t t = rd(TIM17_CNT);
  int r = f->spi ? spi_rd(a, (uint8_t *)b, n) : f->api->fl_read(a, b, n);
  f->rdc += (rd(TIM17_CNT) - t) & 0xFFFFu;
  return r;
}
#define FL_READF(f, a, b, n) fl_rd(f, a, b, n)
#endif
#define FL_READ(a, b, n) FL_READF(CTX(), a, b, n)
#define LIB_BASE 0x110000u /* the SPI module library: 32 slots of 16 KB (k6_lib.h) */
#define LIB_SLOT 0x4000u

static const signed char *cb_fetch(int s, int v, signed char *buf) {
  static const unsigned short base[4] = {0, 512, 1024, 1280};
  FL_READ(CB_ADDR + (base[s] + (unsigned)v) * 20u, buf, 20);
  return buf;
}
static const signed char *ivf_cen_fetch(int s, int c0, signed char *buf);
static int ivf_cell_fetch(int s, int c, int16_t *v);

/* fx_dct[j * 20 + i] (j >= 1): round(32768 sqrt(2/20) cos(pi m / 40)), m = (2i + 1) j */
static const int16_t DCTQ[21] = {10362, 10330, 10235, 10076, 9855, 9573, 9233, 8835, 8383, 7879, 7327,
                                 6730,  6091,  5414,  4704,  3965, 3202, 2419, 1621, 813,  0};
static int dct_at(int j, int i) {
  int m = (2 * i + 1) * j, sg = 1;
  while (m >= 80) m -= 80;
  if (m > 40) m = 80 - m;
  if (m > 20) m = 40 - m, sg = -1;
  return sg * DCTQ[m];
}
#define SLIM_CB_FETCH(s, v, buf) cb_fetch(s, v, (signed char *)(buf))
#define SLIM_IVF_CEN(s, c0, buf) ivf_cen_fetch(s, c0, buf)
#define SLIM_IVF_CELL(s, c, v) ivf_cell_fetch(s, c, v)
#define SLIM_DCT(j, i) dct_at(j, i)
#endif
#ifndef FMS_HOST
#pragma clang diagnostic ignored "-Wunused-function"
#pragma clang diagnostic ignored "-Wunused-const-variable"
#define SOTTO_V36
#define SOTTO_PITCH_THIRD
#define FX_NARROW_MUL
#endif
#include "../../fixed/sotto_fx_slim.c"
#ifndef FMS_HOST
#include "../../fixed/fx_tables.c"
#endif

#ifdef FMS_FLASH
/* The cell deck (16-byte header, then the centroids, each stage's cell starts into one list of
   vector numbers, and that list):
     "K6SC", version 1, IVF_CD, IVF_C0, IVF_C1, IVF_C2, IVF_MAXN, 0, 0, the bytes after the
     header summed (u32)
     centroids: IVF_C0 + IVF_C1 + IVF_C2 of IVF_CD bytes (slim_cells.h's ivf_cen*)
     cell starts: stage s's IVF_Cs + 1 u16 (cell c is vectors [start c, start c + 1) of the list)
     the list: 512 + 512 + 256 u16, each stage's vectors cell by cell, rising in a cell */
#define DK_HDR 16u
#define DK_NC (IVF_C0 + IVF_C1 + IVF_C2)
#define DK_CEN DK_HDR
#define DK_OFF (DK_CEN + DK_NC * IVF_CD)
#define DK_VEC (DK_OFF + 2u * (DK_NC + 3u))
#define DK_BYTES (DK_VEC + 2u * (512u + 512u + 256u))

static uint32_t cell_base(int s) { return s == 0 ? 0u : s == 1 ? (uint32_t)IVF_C0 : (uint32_t)(IVF_C0 + IVF_C1); }

static const signed char *ivf_cen_fetch(int s, int c0, signed char *buf) {
  FL_READ(CTX()->deck + DK_CEN + (cell_base(s) + (uint32_t)c0) * IVF_CD, buf, IVF_BLK * IVF_CD);
  return buf;
}

static int ivf_cell_fetch(int s, int c, int16_t *v) {
  uint16_t ab[2];
  uint32_t d = CTX()->deck, n;
  FL_READ(d + DK_OFF + 2u * (cell_base(s) + (uint32_t)s + (uint32_t)c), ab, 4);
  n = ab[1] > ab[0] ? (uint32_t)(ab[1] - ab[0]) : 0u;
  if (n > IVF_MAXN) n = IVF_MAXN;
  FL_READ(d + DK_VEC + 2u * ab[0], v, 2u * n);
  return (int)n;
}

/* the cell deck's address: the first library slot holding a deck (flags KM_F_LIB | KM_F_CARDS)
   that opens with this build's header and whose bytes add up; 0 if none. tmp: 256 bytes */
static uint32_t find_deck(const fe_t *f, uint8_t *tmp) {
  uint32_t slot, o, i;
  for (slot = 0; slot < 32u; slot++) {
    uint32_t a = LIB_BASE + slot * LIB_SLOT, sum = 0, want;
    const uint8_t *h = tmp, *d = tmp + 16;
    if (FL_READF(f, a, tmp, 32) != 0 || h[0] != 'K' || h[1] != '6' || h[2] != 'M' || (h[13] & 5u) != 5u ||
        (uint32_t)(h[4] | h[5] << 8) < DK_BYTES)
      continue;
    if (d[0] != 'K' || d[1] != '6' || d[2] != 'S' || d[3] != 'C' || d[4] != 1 || d[5] != IVF_CD || d[6] != IVF_C0 ||
        d[7] != IVF_C1 || d[8] != IVF_C2 || d[9] != IVF_MAXN)
      continue;
    want = (uint32_t)d[12] | (uint32_t)d[13] << 8 | (uint32_t)d[14] << 16 | (uint32_t)d[15] << 24;
    for (o = DK_HDR; o < DK_BYTES; o += 256u) {
      uint32_t n = DK_BYTES - o < 256u ? DK_BYTES - o : 256u;
      if (FL_READF(f, a + 16u + o, tmp, n) != 0) break;
      for (i = 0; i < n; i++) sum += tmp[i];
    }
    if (o >= DK_BYTES && sum == want) return a + 16u;
  }
  return 0;
}
#endif

#ifdef FMS_HOST
/* fmsotto_host COUNTS.i8 OUT.bit [OUT.raw] [CAV]: counts (int8, one a 64-sample group) in at a
   steady rate (CAV: cycles a half, Q4), superframes out, and the 8 kHz audio if asked */
#ifdef FMS_SPI
/* SPI flash as the K6 has it, for the reads: sotto's codebook at 0x100000 and the cell deck
   (FMS_DECK, a file from tools/k6_fmsotto.py deck) in library slot 14 */
static uint8_t *g_flash;
static int host_fl_read(uint32_t a, void *buf, uint32_t n) {
  if (a < 0x100000u || a + n > 0x200000u) return -1;
  memcpy(buf, g_flash + (a - 0x100000u), n);
  return 0;
}
static void host_flash(fe_t *f) {
  static const unsigned short base[3] = {0, 512, 1024};
  const char *path = getenv("FMS_DECK");
  uint8_t *slot, tmp[256];
  FILE *fd;
  long n;
  int s;
  g_flash = calloc(1, 0x100000u);
  for (s = 0; s < 3; s++)
    memcpy(g_flash + 0x20u + base[s] * 20u, fx_cb[s], (size_t)(1u << fx_cb_bits[s]) * 20u);
  slot = g_flash + (LIB_BASE - 0x100000u) + 14u * LIB_SLOT;
  if (!path || !(fd = fopen(path, "rb"))) {
    fprintf(stderr, "fmsotto_host: set FMS_DECK to the cell deck\n");
    exit(2);
  }
  n = (long)fread(slot + 16, 1, LIB_SLOT - 24u, fd);
  fclose(fd);
  slot[0] = 'K', slot[1] = '6', slot[2] = 'M', slot[3] = 1;
  slot[4] = (uint8_t)n, slot[5] = (uint8_t)(n >> 8), slot[13] = 5, slot[15] = 14;
  g_fe = f;
  f->deck = find_deck(f, tmp);
  if (!f->deck) {
    fprintf(stderr, "fmsotto_host: the deck doesn't check\n");
    exit(2);
  }
}
#endif

int main(int argc, char **argv) {
  static fe_t f;
  slim_enc_t *e;
  FILE *fi, *fo, *fa = NULL;
  int c, n = 0, k = 0;
  uint32_t pr = 0, ri = 0, lost = 0, cav, acc = 0;
  int16_t x[BLK];
  unsigned char out[SLIM_BYTES];
  if (argc < 3) return fprintf(stderr, "usage: fmsotto_host COUNTS.i8 OUT.bit [OUT.raw] [CAV]\n"), 2;
  fi = fopen(argv[1], "rb"), fo = fopen(argv[2], "wb");
  if (argc > 3 && strcmp(argv[3], "-")) fa = fopen(argv[3], "wb");
  if (!fi || !fo) return 1;
  fe_init(&f);
#ifdef FMS_SPI
  host_flash(&f);
#endif
  cav = argc > 4 ? (uint32_t)atoi(argv[4]) : CAV0;
  e = calloc(1, slim_enc_bytes());
  slim_enc_init(e);
  while ((c = fgetc(fi)) != EOF) {
    fe_count(&f, (int8_t)c);
    if (++k == (int)(HALF / 64u)) {
      k = 0;
      acc += cav; /* whole cycles a half, dithered to the fraction */
      fe_rate(&f, acc >> 4);
      acc &= 15;
      while (fe_take(&f, &pr, &ri, &lost, x)) {
        if (fa) fwrite(x, 2, BLK, fa);
        if (slim_encode10(e, x, out)) fwrite(out, 1, SLIM_BYTES, fo), n++;
      }
    }
  }
  while (slim_flush(e, out)) fwrite(out, 1, SLIM_BYTES, fo), n++;
  fprintf(stderr, "fmsotto_host: %d superframes, lost=%u pk=%u\n", n, lost, f.pk);
  fclose(fo);
  if (fa) fclose(fa);
  return 0;
}
#else

/* DMA1_Channel1: one half of the ring ready (HTIF: the first, TCIF: the second) */
static void dma_isr(void) {
  fe_t *f = (fe_t *)rd(SRAM0 + 4u * V_CTX);
  uint32_t t0 = rd(TIM17_CNT) & 0xFFFF, fl = rd(DMA_ISR) & 7u, i;
  const uint32_t *w;
  wr(DMA_IFCR, 0xFu);
  if ((fl & 6u) == 6u) f->miss++;
  fe_rate(f, (t0 - f->c17) & 0xFFFF);
  f->c17 = t0;
  w = (const uint32_t *)(f->ring + ((fl & 4u) && !(fl & 2u) ? 0u : HALF));
  for (i = 0; i < HALF / 4u; i += 16) { /* 64 samples: PB1 in each byte */
    /* four words a turn (one at a time, the loop was most of the handler's time) */
    const uint32_t *q = w + i, m = 0x02020202u;
    uint32_t a = 0, j;
    for (j = 0; j < 16; j += 4, q += 4) a += (q[0] & m) + (q[1] & m) + (q[2] & m) + (q[3] & m);
    a >>= 1;
    a = (a & 0x00FF00FFu) + ((a >> 8) & 0x00FF00FFu);
    fe_count(f, (int)((a & 0xFFFF) + (a >> 16)) * 2 - 64);
  }
  f->halves++;
  if (++f->quiet > MAX_IRQ || f->halves > MAX_HALVES) {
    wr(DMA_CCR1, rd(DMA_CCR1) & ~6u);
    wr(NVIC_ICER, 1u << IRQ_DMA1);
  }
  f->isr += ((rd(TIM17_CNT) & 0xFFFF) - t0) & 0xFFFF;
}

static char *put(char *p, const char *s) {
  while (*s) *p++ = *s++;
  return p;
}

static char *hex(char *p, uint32_t v, int digits) {
  const char *x = "0123456789abcdef";
  while (digits--) *p++ = x[(v >> (4 * digits)) & 15];
  return p;
}

static char *dec(char *p, uint32_t v) {
  char t[10];
  int n = 0;
  do {
    t[n++] = (char)('0' + v % 10u);
    v /= 10u;
  } while (v);
  while (n) *p++ = t[--n];
  return p;
}

static char *kv(char *p, const char *k, uint32_t v) { return dec(put(p, k), v); }

/* The outbox (API level 15, Jeff 2026-11-20: "Core SPI outbox"): 64 KB of SPI flash at
   KM_OB_LO that the core lets a module program and erase, for a sender module to read back.
     0x000  "K6OB", 1, the codec (KM_SOTTO_1200), 0, 0, superframes (u32), their bytes summed (u32)
     0x100  the superframes, 6 bytes each, back to back
   The run erases the sectors it may fill before the DMA starts, records from the first carrier
   it hears (RSSI over FMS_SQ_DBM) until the carrier has been gone 1 s (the module then ends),
   and writes the header last, so a recording cut short reads as empty (0xFF). */
static void ob_put(fe_t *f, const unsigned char *b, uint32_t n) {
  while (n) {
    uint32_t a = KM_OB_LO + OB_DATA + f->obw, k = 256u - (a & 255u), i;
    if (k > n) k = n;
    if (f->obw + k > OB_CAP) {
      f->rec = 2;
      return;
    }
    f->api->fl_prog(a, b, k);
    for (i = 0; i < k; i++) f->obs += b[i];
    f->obw += k, b += k, n -= k;
  }
}

/* "s N hex": the *nf superframes in fr, N the number of the first (and into the outbox while
   recording); returns the count after them */
static uint32_t say_frames(fe_t *f, char *line, const unsigned char *fr, uint32_t *nf, uint32_t n) {
  const k6_api_t *api = f->api;
  uint32_t i;
  char *p = dec(put(line, "s "), n);
  if (f->rec == 1) ob_put(f, fr, *nf * SLIM_BYTES);
  *p++ = ' ';
  for (i = 0; i < *nf * SLIM_BYTES; i++) p = hex(p, fr[i], 2);
  *p = 0;
  api->say(line);
  n += *nf;
  *nf = 0;
  return n;
}

/* SPI1's clock. Aura leaves it at PCLK / 4 (BR = 1, CR1 034f, docs/k6-stock-firmware.md); the
   codebook reads are most of a superframe's bytes, so FMS_FAST tries PCLK / 2 (BR = 0) and keeps
   it only if the cell deck reads back whole at that speed (find_deck again). FMS_FAST 2 (the
   default) swaps the two every 10 s so one run shows both (the second's line says div=); 1 keeps
   PCLK / 2; 0 never tries it.
   Changed only between reads, SPE off while BR moves; Aura's CR1 goes back at the end. */
#ifndef FMS_FAST
#define FMS_FAST 2
#endif
static void spi_cr1(uint16_t cr1) {
  uint32_t k;
  for (k = 0; (R16(SPI_SR) & 0x80u) && k < SPIN; k++) {
  }
  R16(SPI_CR1) = (uint16_t)(cr1 & ~0x40u);
  R16(SPI_CR1) = cr1;
}
int k6_main(const k6_api_t *api) {
  uint32_t room = 0, i, apb2, ahb, cfgr1, acr, v25, v46, t0, now, tsec = 0, sec = 0;
  uint32_t pr = 0, ri = 0, lost = 0, n = 0, nf = 0, pw0 = 0, l0 = 0, r0 = 0, c0 = 0, tq = 0, tc = 0, ob;
  uint16_t r33, r34, r7e, r13, cr1 = 0, cr1f = 0;
  char line[64], *p;
  unsigned char fr[4 * SLIM_BYTES];
  int16_t x[BLK];
  fe_t *f;
  slim_enc_t *e;
  int ret = 0, key = 0;
  uint8_t *s = (uint8_t *)(((uint32_t)api->scratch(&room) + 3) & ~3u);
  if (api->level < 5) return -3;
  if (room < RSZ + sizeof(fe_t) + slim_enc_bytes() + 8) return -8;
  f = (fe_t *)(s + RSZ);
  e = (slim_enc_t *)(void *)(f + 1);
  fe_init(f);
  f->api = api;
  f->ring = s;
  f->deck = find_deck(f, s); /* (the ring as its buffer, before the DMA has it) */
  if (!f->deck) return -10;
  /* the vector copy: the bootloader's DMA entry, the firmware's own HardFault (fmso1a-f checked
     the whole table; this is the room-saving form) */
  if (rd(SRAM0 + 4u * V_DMA1) != rd(BOOT_VEC + 4u * V_DMA1) || rd(SRAM0 + 4u * V_HF) == rd(BOOT_VEC + 4u * V_HF))
    return -9;
  /* the upload's carrier gone */
  t0 = api->ms();
  while (KM_RSSI_DBM(api->fd_read(0x67)) > -80 && api->ms() - t0 < 150000) {
    if (api->key_poll()) return -2;
    api->delay_us(10000);
  }
  if (rd(GPIOB) & (3u << 2)) return -4;
  if (api->level >= 6) {
    api->lcd_text(0, "  FM SOTTO");
    api->lcd_text(2, "  RECORDING A CALL");
  }
  api->tune(api->rx_hz(), FMS_WIDE);
  api->delay_us(50000);
  ob = api->level >= 15; /* the outbox: its sectors erased now, before the interrupt runs */
  for (i = 0; ob && i < OB_SECTORS; i++)
    if (api->fl_erase(KM_OB_LO + 4096u * i)) ob = 0;
  apb2 = rd(RCC_APB2ENR), ahb = rd(RCC_AHBENR);
  wr(RCC_AHBENR, ahb | 1u);
  if ((apb2 & TIM17EN) || (rd(DMA_CCR1) & 1)) {
    wr(RCC_AHBENR, ahb);
    return -7;
  }
  r33 = api->fd_read(0x33), r34 = api->fd_read(0x34);
  if ((r33 & 0x0100) && api->fd_write(0x33, (uint16_t)(r33 & ~0x0100)) != 0) {
    wr(RCC_AHBENR, ahb);
    return -5;
  }
  api->fd_write(0x34, (uint16_t)((r34 & 0xFFF0) | 13));
  r7e = api->fd_read(0x7E), r13 = api->fd_read(0x13);
  if (GAIN13) {
    api->fd_write(0x7E, (uint16_t)((r7e & 0x0FFF) | 0x8000 | (3u << 12)));
    api->fd_write(0x13, GAIN13);
  }
  wr(RCC_APB2ENR, apb2 | SYSCFGEN | TIM17EN);
  cfgr1 = rd(SYSCFG_CFGR1);
  acr = rd(FLASH_ACR);
  v25 = rd(SRAM0 + 4u * V_DMA1);
  v46 = rd(SRAM0 + 4u * V_CTX);
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x28, 0);
  wr(TIM17 + 0x2C, 0xFFFF);
  wr(TIM17 + 0x14, 1);
  /* its update request on, as gp0rx.h and aprsclk.c have it: that is what starts DMA channel 1
     (then it runs flat out). fmso1a ran without it only because an earlier module had left it
     set; after a power cycle (fmso1d) the DMA never moved */
  wr(TIM17 + 0x10, 0);
  wr(TIM17 + 0x0C, 1u << 8);
  wr(TIM17 + 0x00, 1);
  f->c17 = rd(TIM17_CNT) & 0xFFFF;
#ifndef FMS_API_READS
  f->spi = (R16(SPI_CR1) & 0x44u) == 0x44u; /* SPE and MSTR: as Aura left it */
#endif
  cr1 = R16(SPI_CR1);
  if (FMS_FAST && f->spi && (cr1 & 0x38u) == 0x08u) { /* BR = 1, as Aura left it: try BR = 0, and
    keep it if the deck is found again through it (the encoder's memory as the buffer, before its init) */
    spi_cr1((uint16_t)(cr1 & ~0x38u));
    if (find_deck(f, (uint8_t *)e) == f->deck)
      cr1f = R16(SPI_CR1), f->spi = 2;
    else
      spi_cr1(cr1);
  }
  slim_enc_init(e);
  /* the handler in: its state, its vector, SRAM at 0, then the DMA with both interrupts */
  wr(SRAM0 + 4u * V_CTX, (uint32_t)f);
  wr(SRAM0 + 4u * V_DMA1, (uint32_t)&dma_isr | 1u);
  wr(SYSCFG_CFGR1, (cfgr1 & ~3u) | 3u);
  wr(DMA_CCR1, 0);
  wr(DMA_IFCR, 0xF);
  wr(DMA_CPAR1, GPIOB + 0x10);
  wr(DMA_CMAR1, (uint32_t)s);
  wr(DMA_CNDTR1, RSZ);
  wr(NVIC_ICPR, 1u << IRQ_DMA1);
  wr(NVIC_ISER, 1u << IRQ_DMA1);
  wr(DMA_CCR1, (3u << 12) | (1u << 7) | (1u << 5) | (1u << 2) | (1u << 1) | 1u);
  api->delay_us(20000); /* the rate settles */
  p = kv(put(line, "fms start"), " ks=", 393216000u / f->cav);
  p = kv(p, " spi=", f->spi);
  *p = 0;
  api->say(line);
  pr = f->pw, ri = f->wi, pw0 = f->pw, c0 = f->isr, r0 = f->rdc;
  pr -= pr % BLK, ri -= ri % BLK; /* blocks start where the ring's do */
  t0 = tsec = tq = api->ms();
  while (f->halves) { /* none in the 20 ms: the DMA isn't running, so nothing to code (-11) */
    /* the clock and the keys each time round, not only when the encoder has caught up: fmso1a
       ran behind for minutes and never got here, so no key or time limit could end it */
    f->quiet = 0;
    now = api->ms();
    if (now - tq >= 50) {
      tq = now;
      key = api->key_poll();
      if (key || now - t0 >= RUN_MS || f->rec == 2) break;
      if (ob) { /* the outbox records one call: from its carrier until 1 s after it drops */
        if (KM_RSSI_DBM(api->fd_read(0x67)) > FMS_SQ_DBM)
          f->rec |= 1, tc = now;
        else if (f->rec && now - tc > 1000)
          break;
      }
    }
    if (now - tsec >= 1000) { /* the second's report */
      uint32_t pw = f->pw, c = f->isr, dt = now - tsec, pk = f->pk;
      f->pk = 0;
      p = dec(put(line, "fms "), ++sec);
      p = kv(p, " sps=", (pw - pw0) * 1000u / dt);
      p = kv(p, " pk=", pk);
      p = kv(p, " skip=", lost - l0);
      p = kv(p, " miss=", f->miss);
      p = kv(p, " isr=", (c - c0) / (dt * 960u));
      p = kv(p, " rd=", (f->rdc - r0) / (dt * 960u));
      if (cr1f) p = kv(p, " div=", R16(SPI_CR1) == cr1f ? 2u : 4u);
      *p = 0;
      api->say(line);
      pw0 = pw, c0 = c, l0 = lost, r0 = f->rdc, tsec = now;
      if (cr1f && FMS_FAST == 2 && sec % 10u == 0) spi_cr1(R16(SPI_CR1) == cr1f ? cr1 : cr1f);
    }
    if (fe_take(f, &pr, &ri, &lost, x) && slim_encode10(e, x, fr + SLIM_BYTES * nf) && ++nf == 4)
      n = say_frames(f, line, fr, &nf, n);
  }
  /* everything back: the interrupt off first, then the DMA, the map, the vector */
  wr(NVIC_ICER, 1u << IRQ_DMA1);
  wr(DMA_CCR1, 0);
  wr(DMA_IFCR, 0xF);
  wr(NVIC_ICPR, 1u << IRQ_DMA1);
  wr(SYSCFG_CFGR1, cfgr1);
  wr(FLASH_ACR, acr);
  wr(SRAM0 + 4u * V_DMA1, v25);
  wr(SRAM0 + 4u * V_CTX, v46);
  /* the encoder's last superframes (the codebook reads need the context entry: put it back
     for them, with the interrupt off) */
  wr(SRAM0 + 4u * V_CTX, (uint32_t)f);
  while (slim_flush(e, fr + SLIM_BYTES * nf))
    if (++nf == 4) n = say_frames(f, line, fr, &nf, n);
  wr(SRAM0 + 4u * V_CTX, v46);
  if (cr1f) spi_cr1(cr1); /* Aura's clock back */
  if (nf) n = say_frames(f, line, fr, &nf, n);
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x0C, 0);
  wr(RCC_APB2ENR, apb2);
  wr(RCC_AHBENR, ahb);
  if (GAIN13) api->fd_write(0x13, r13), api->fd_write(0x7E, r7e);
  api->fd_write(0x34, r34);
  if (r33 & 0x0100) api->fd_write(0x33, r33);
  p = kv(put(line, "fms end"), " n=", n);
  p = kv(p, " lost=", lost);
  p = kv(p, " miss=", f->miss);
  p = kv(p, " h=", f->halves);
  if (f->obw) { /* the header last */
    uint32_t h[4] = {0x424F364Bu, 1u | KM_SOTTO_1200 << 8, f->obw / SLIM_BYTES, f->obs}; /* "K6OB" */
    api->fl_prog(KM_OB_LO, h, 16);
    p = kv(p, " ob=", f->obw / SLIM_BYTES);
  }
  *p = 0;
  api->say(line);
  ret = !f->halves ? -11 : key && !n ? -2 : (int)n;
  return ret;
}
#endif
