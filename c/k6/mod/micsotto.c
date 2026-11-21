/* micsotto: the K6's own mic, coded live as sotto 1200, then played back on its speaker
   (2026-11-21, Jeff: "perform a Sotto encoding of recorded voice with the K6";
   docs/k6-mic-probe.md).

   The mic reaches the MCU only while the chip is keyed with the PA off, with the mic ADC and the
   AF DAC on (REG_30 bits 2, 9; micaf, run maf1a), on AF select 4, where PA11's one-pin converter
   (pa11adc.h) reads it, and only with the programming cable out of the jack. REG_7D's mic
   sensitivity at 31 is the most (micgain, mgn1b/mgn1d). mickrec records it as ADPCM, but the
   converter takes the whole CPU while it runs, so a recording is as long as the RAM. Here the
   converter runs from TIM17's interrupt instead, W_IRQ turns every 125 us (an 8 kHz sample;
   about half the CPU), and the foreground runs the slim sotto 1200 encoder as fmsotto does (its
   SPI glue copied from fmsotto.c: the codebook and the cell deck read from SPI flash), so 6 bytes
   cover 40 ms and the scratch holds MAX_SF superframes.

   Between the turns the converter's capacitor keeps integrating the audio through its
   resistors (4.7k, 3.9 nF: 18 us), so the next turns count that charge too: the gap scales the
   signal, it doesn't break it.

   The handler: pa11_sample, the DC tracked off (a one-pole high-pass, ~160 Hz), the chip's
   transmit pre-emphasis undone (a one-pole low-pass at 300 Hz, as tools/k6_micprobe.py's deemph)
   into a ring of 8 kHz samples. The foreground: 80 samples at a time through a block AGC (peak to
   AGC_TOP, gain AGC_MIN .. AGC_MAX in Q8) into slim_encode10.

   The run, cable out (cableout.h): PULL CABLE / THEN ANY KEY; the call (AG7EW in CW at full power
   on the bench frequency, keyed down after) that a job's pluto_bench cue answers with a voice on
   SOUND_HZ (MS_CUE 1: the module waits for that carrier to stay HOLD_MS) or, MS_CUE 0, TALK NOW
   on the screen for a person at the mic; keyed with the PA off and the mic on, REC_MS of coding;
   keyed down, a last CW ID; PLUG CABLE IN / THEN ANY KEY; then the lines
     micsotto start 30=bff1 7d=e95c sf=60
     mso ms=8000 sps=8000 pk=1234 g=0400 isr=48 rd=12      (the recording: its length, samples
                                    a second, largest sample, last gain, CPU % in the handler and
                                    in the SPI reads)
     s 0 0123456789ab...                     (superframes 0-3: 4 x 6 bytes, hex: k6_fmsotto.py log)
     micsotto end n=200 lost=0 e=0
   and the superframes go to the core's message store (sotto_play): once the module returns the
   K6 plays them on its speaker, as VOICE MSG plays a message, and its play key plays them again.

   The mic is never on the air: its bit is set only while REG_36 says the PA is off, checked each
   second. Writes, all put back: REG_30, REG_47, REG_48, REG_7D, the tuning, PA11's mode, speed and
   output bit, PB2 (the speaker amplifier), SYSCFG CFGR1 and FLASH_ACR, the vector copy's entries
   38 (TIM17) and 46, NVIC bit 22, TIM17 and its clock enable. Returns the superframes, or -2 (not
   on the bench frequency), -3 (tx_on refused), -4 (the bench frequency busy), -5 (the PA would not
   go off, or REG_30 did not take the bits), -6 (a key mid-run), -7 (no key), -8 (scratch), -9 (the
   vector copy isn't the bootloader's table), -10 (no cell deck), -11 (no samples: the interrupt
   never ran), -12 (TIM17 in use), -13 (no sound within WAIT_MS). Level 13.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 13
#define K6_MODULE_OPT z
#include "../k6_mod.h"
#include "m0rt.h"
#include "micadc.h"
#include "pa11adc.h"
#include "cableout.h"
K6_MODULE_NAME("MIC SOTTO");

#ifndef MS_CUE
#define MS_CUE 1
#endif
#ifndef REC_MS
#define REC_MS 8000u
#endif
#ifndef MAX_SF
#define MAX_SF 500u /* 20 s: under the message store's 3,200 B */
#endif
#ifndef W_IRQ
#define W_IRQ 96u /* converter turns a sample: ~61 us of the 125 */
#endif
#ifndef HOLD
#define HOLD 3u
#endif
#ifndef SEL
#define SEL 4u
#endif
#ifndef MIC_SENS
#define MIC_SENS 31u
#endif
#ifndef HOLD_MS
#define HOLD_MS 5000u /* the cue's carrier: past the Pluto's MCW ID (3.7 s) */
#endif
#ifndef WAIT_MS
#define WAIT_MS 60000u
#endif
#ifndef DROP_MS
#define DROP_MS 1000u
#endif
#ifndef KEY_WAIT_MS
#define KEY_WAIT_MS 1200000u
#endif
#ifndef AGC_TOP
#define AGC_TOP 16000 /* a block's peak after the gain */
#endif
#ifndef AGC_G0
#define AGC_G0 1024 /* Q8: x4 */
#endif
#define AGC_MIN 64
#define AGC_MAX 8192
#define SOUND_HZ 445850000u
#define BENCH_HZ 440850000u
#define RSSI_ON -90
#define AF_DAC 0x0200u
#define AMP (1u << 2)
#define CW_TEXT "AG7EW"
#define UNIT_US 60000u
#define TX_MS (REC_MS + 4000u)
#define PCM_N 240 /* 8 kHz samples kept: three encoder blocks */
#define BLK 80
#define T_PER 12000u /* TIM17 counts at 96 MHz: an 8 kHz period */

#define RCC_APB2ENR 0x40021018u
#define TIM17 0x40014800u
#define TIM17_CNT (TIM17 + 0x24)
#define SYSCFG_CFGR1 0x40010000u
#define FLASH_ACR 0x40022000u
#define NVIC_ISER 0xE000E100u
#define NVIC_ICER 0xE000E180u
#define NVIC_ICPR 0xE000E280u
#define SRAM0 0x20000000u
#define BOOT_VEC 0x08000000u
#define V_HF 3u
#define V_TIM17 (16u + 22u)
#define V_CTX 46u
#define IRQ_TIM17 22u
#define TIM17EN (1u << 18)
#define SYSCFGEN 1u
#define CB_ADDR 0x100020u

typedef struct {
  const k6_api_t *api;
  uint32_t m_in, deck, rdc, spi;
  pa11_t c;
  int32_t lp, de; /* the high-pass's low-pass (Q8), the de-emphasis (Q4) */
  uint32_t n0, wi; /* samples skipped at the start (the converter settles), the ring's write index */
  volatile uint32_t pw, pk, isr, run;
  int16_t pcm[PCM_N];
} st_t;

static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }
static int32_t sat16(int32_t v) { return v > 32767 ? 32767 : v < -32767 ? -32767 : v; }

/* the next 10 ms block into x, or 0 when there isn't one yet; *lost counts blocks skipped
   because the encoder fell behind the interrupt */
static int take(st_t *f, uint32_t *pr, uint32_t *ri, uint32_t *lost, int16_t *x) {
  uint32_t pw = f->pw, i;
  if (pw - *pr < BLK) return 0;
  while (pw - *pr > PCM_N - BLK / 2) {
    *pr += BLK, (*lost)++;
    if ((*ri += BLK) == PCM_N) *ri = 0;
  }
  for (i = 0; i < BLK; i++) x[i] = f->pcm[*ri + i];
  *pr += BLK;
  if ((*ri += BLK) == PCM_N) *ri = 0;
  return 1;
}

/* ---- the encoder: sotto's slim 1200, its read-only data in SPI flash (as fmsotto.c) ---- */
#define CTX() ((const st_t *)rd(SRAM0 + 4u * V_CTX))
/* SPI flash reads. Aura's fl_read runs from main flash, and with SRAM mapped at 0 (MEM_MODE 3)
   the flash prefetch is off (docs/k6-modules.md, txdog), so it runs slowly: on the radio
   (fmso1a) the encoder kept up with only 80 % of real time. So the encoder's reads go to SPI1
   directly from RAM, as stockfont.c does (READ 0x03 only; SPI1 as Aura left it, soft NSS on
   PA2), a byte at a time with bounded waits, interrupts on (no other SPI1 user runs while the
   module does; the converter's handler never reads the flash). The time in reads is counted on TIM17. */
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
     nothing is queued, so the converter's handler can interrupt anywhere. fmso1e, a byte at a time:
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
static int fl_rd(const st_t *cf, uint32_t a, void *b, uint32_t n) {
  st_t *f = (st_t *)cf;
  uint32_t t = rd(TIM17_CNT), d;
  int r = f->spi ? spi_rd(a, (uint8_t *)b, n) : f->api->fl_read(a, b, n);
  d = rd(TIM17_CNT) - t;
  f->rdc += (int32_t)d < 0 ? d + T_PER : d; /* (TIM17 counts 0 .. T_PER - 1: an 8 kHz period) */
  return r;
}
#define FL_READF(f, a, b, n) fl_rd(f, a, b, n)
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
#pragma clang diagnostic ignored "-Wunused-function"
#pragma clang diagnostic ignored "-Wunused-const-variable"
#define SOTTO_V36
#define SOTTO_PITCH_THIRD
#define FX_NARROW_MUL
#include "../../fixed/sotto_fx_slim.c"
#include "../../fixed/fx_tables.c"

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
static uint32_t find_deck(const st_t *f, uint8_t *tmp) {
  uint32_t slot;
  for (slot = 0; slot < 32u; slot++) {
    uint32_t a = LIB_BASE + slot * LIB_SLOT;
    const uint8_t *h = tmp, *d = tmp + 16;
    if (FL_READF(f, a, tmp, 32) != 0 || h[0] != 'K' || h[1] != '6' || h[2] != 'M' || (h[13] & 5u) != 5u ||
        (uint32_t)(h[4] | h[5] << 8) < DK_BYTES)
      continue;
    if (d[0] != 'K' || d[1] != '6' || d[2] != 'S' || d[3] != 'C' || d[4] != 1 || d[5] != IVF_CD || d[6] != IVF_C0 ||
        d[7] != IVF_C1 || d[8] != IVF_C2 || d[9] != IVF_MAXN)
      continue;
    return a + 16u; /* (fmsotto checks the deck's sum too; no room here: fmsotto runs prove the deck) */
  }
  return 0;
}

/* TIM17's update: one 8 kHz sample from W_IRQ turns of the converter into the ring */
static void tim_isr(void) {
  st_t *f = (st_t *)rd(SRAM0 + 4u * V_CTX);
  uint32_t t0 = rd(TIM17_CNT), y, a, t1;
  int32_t d;
  wr(TIM17 + 0x10, 0); /* UIF */
  if (!f->run) return;
  y = pa11_sample(f->m_in, HOLD, W_IRQ, &f->c);
  if (f->n0) { /* the first two aren't differences yet; the high-pass starts from the third */
    if (--f->n0 == 0) f->lp = (int32_t)(y << 8);
    return;
  }
  f->lp += ((int32_t)(y << 8) - f->lp) >> 3;   /* ~160 Hz */
  d = ((int32_t)(y << 8) - f->lp) >> 4;        /* Q4 */
  f->de += ((d - f->de) * 27) >> 7;            /* 300 Hz: the pre-emphasis undone */
  d = sat16(f->de);
  f->pcm[f->wi] = (int16_t)d;
  if (++f->wi == PCM_N) f->wi = 0;
  f->pw++;
  a = (uint32_t)(d < 0 ? -d : d);
  if (a > f->pk) f->pk = a;
  t1 = rd(TIM17_CNT);
  f->isr += t1 >= t0 ? t1 - t0 : t1 + T_PER - t0;
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

static int rssi(const k6_api_t *api) { return KM_RSSI_DBM(api->fd_read(0x67)); }

static int code_of(char c) {
  static const char tab[] = "~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";
  int i;
  for (i = 2; i < (int)sizeof tab - 1; i++)
    if (tab[i] == c) return i;
  return 0;
}

static int hold(const k6_api_t *api, int on, int n) {
  if (api->tx_pa(on) < 0) return -5;
  while (n--) {
    if (api->key_poll()) return -6;
    api->delay_us(UNIT_US);
  }
  return 0;
}

/* the call: AG7EW in CW at full power on the bench frequency, keyed down after */
static int call(const k6_api_t *api) {
  const char *s;
  int e;
  if (rssi(api) >= RSSI_ON) return -4;
  if (api->tx_on(TX_MS) != 0) return -3;
  e = hold(api, 0, 5);
  for (s = CW_TEXT; *s && !e; s++) {
    int cd = code_of(*s), b;
    for (b = 31; !(cd >> b); b--) {}
    while (b-- && !e) {
      e = hold(api, 1, (cd >> b) & 1 ? 3 : 1);
      if (!e) e = hold(api, 0, b ? 1 : 3);
    }
  }
  api->tx_off();
  return e;
}

/* on SOUND_HZ: (drop) the carrier gone DROP_MS, else a carrier that stays HOLD_MS; 0, -6, -13 */
static int sound(const k6_api_t *api, int drop, int wide) {
  uint32_t t0 = api->ms(), t1 = t0, up = 0, home = api->rx_hz();
  int e = 0;
  api->tune(SOUND_HZ, wide);
  for (;;) {
    int on = rssi(api) >= RSSI_ON;
    if (drop) {
      if (on) t1 = api->ms();
      else if (api->ms() - t1 >= DROP_MS) break;
    } else if (!on) {
      up = 0;
    } else if (!up) {
      up = 1, t1 = api->ms();
    } else if (api->ms() - t1 >= HOLD_MS) {
      break;
    }
    if (api->key_poll()) e = -6;
    else if (api->ms() - t0 >= WAIT_MS) e = -13;
    if (e) break;
    api->delay_us(20000);
  }
  api->tune(home, wide);
  api->delay_us(300000);
  return e;
}

static void say_frames(const k6_api_t *api, char *line, const uint8_t *sf, uint32_t i, uint32_t n) {
  uint32_t k;
  char *p = dec(put(line, "s "), i);
  *p++ = ' ';
  for (k = 0; k < n * SLIM_BYTES; k++) p = hex(p, sf[i * SLIM_BYTES + k], 2);
  *p = 0;
  api->say(line);
}

int k6_main(const k6_api_t *api) {
  uint32_t moder = rd(PA11_GPIOA + PA11_MODER), odr = rd(PA11_GPIOA + PA11_ODR) & (1u << PA11_PIN),
           ospeed = rd(PA11_GPIOA + PA11_OSPEEDR), amp = rd(MIC_GPIOB + PA11_ODR) & AMP, home = api->rx_hz();
  uint32_t room = 0, i, apb2, cfgr1, acr, v38, v46, t0, now, cap, s30 = 0, want;
  uint32_t pr = 0, ri = 0, lost = 0, n = 0, pw0 = 0, dt = 0, pkm = 0;
  uint16_t r47 = api->fd_read(0x47), r48 = api->fd_read(0x48), r7d = api->fd_read(0x7D);
  int32_t g = AGC_G0;
  char line[64], *p;
  int16_t x[BLK];
  uint8_t *s = (uint8_t *)(((uint32_t)api->scratch(&room) + 3) & ~3u), *sf;
  st_t *f;
  slim_enc_t *e;
  int ret = 0, er = 0, wide = api->fd_read(0x43) == 0x3028;
  if (api->level < 6) return -1;
  f = (st_t *)s;
  e = (slim_enc_t *)(void *)(f + 1);
  sf = (uint8_t *)e + slim_enc_bytes();
  if (room < sizeof(st_t) + slim_enc_bytes() + 50u * SLIM_BYTES) return -8;
  cap = (room - (uint32_t)(sf - s)) / SLIM_BYTES;
  if (cap > MAX_SF) cap = MAX_SF;
  memset(f, 0, sizeof *f);
  f->api = api;
  f->m_in = moder & ~(3u << (2 * PA11_PIN));
  f->deck = find_deck(f, (uint8_t *)e); /* (the encoder's memory as the buffer, before its init) */
  p = hex(put(line, "micsotto start 30="), api->fd_read(0x30), 4);
  p = hex(put(p, " 7d="), r7d, 4);
  p = kv(p, " sf=", cap);
  *p = 0;
  api->say(line);
  if (!f->deck) return -10;
  if (home != BENCH_HZ) return -2;
  if (rd(SRAM0 + 4u * V_TIM17) != rd(BOOT_VEC + 4u * V_TIM17) || rd(SRAM0 + 4u * V_HF) == rd(BOOT_VEC + 4u * V_HF))
    return -9;
  apb2 = rd(RCC_APB2ENR);
  if (apb2 & TIM17EN) return -12;
  wr(MIC_GPIOB + MIC_BRR, AMP);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed | (3u << (2 * PA11_PIN)));
  co_show(api, "MIC SOTTO CABLE OUT", "PULL CABLE", "THEN ANY KEY");
  if (co_key(api, KEY_WAIT_MS) < 0) {
    ret = -7;
    goto back;
  }
  co_show(api, "MIC SOTTO CABLE OUT", "QUIET PLEASE", "CALLING");
  for (t0 = now = api->ms(); api->ms() - t0 < 30000u && api->ms() - now < 1000u;) {
    if (rssi(api) >= RSSI_ON) now = api->ms();
    api->delay_us(20000);
  }
  er = call(api);
#if MS_CUE
  if (!er) er = sound(api, 0, wide);
#else
  if (!er) {
    co_show(api, "MIC SOTTO CABLE OUT", "TALK NOW", "");
    api->delay_us(500000);
  }
#endif
  if (er) goto back;
  co_show(api, "MIC SOTTO CABLE OUT", "RECORDING", "");
  /* keyed with the PA off, the mic on (mickrec's record()) */
  if (rssi(api) >= RSSI_ON) er = -4;
  else if (api->tx_on(TX_MS) != 0) er = -3;
  else if (api->tx_pa(0) < 0) er = -5;
  if (!er) {
    s30 = api->fd_read(0x30);
    if (api->fd_read(0x36) & 0x0080u) er = -5;
  }
  want = (s30 | AF_DAC | MIC_ADC) & 0xFFFFu;
  if (!er && api->fd_read(0x30) != want && api->fd_write(0x30, (uint16_t)want) != 0) mic_bus_write(api, 0x30, want);
  if (!er && api->fd_read(0x30) != want) er = -5;
  if (er) goto keyed;
  api->fd_write(0x47, (uint16_t)((r47 & ~0x0F00u) | (SEL << 8)));
  api->fd_write(0x48, (uint16_t)(r48 | 0x03F0u));
  api->fd_write(0x7D, (uint16_t)((r7d & ~0x001Fu) | MIC_SENS));
  f->spi = (R16(SPI_CR1) & 0x44u) == 0x44u;
  slim_enc_init(e);
  /* TIM17 at 8 kHz, its update interrupt through the vector copy, SRAM at 0 (fmsotto's way in) */
  wr(RCC_APB2ENR, apb2 | SYSCFGEN | TIM17EN);
  cfgr1 = rd(SYSCFG_CFGR1);
  acr = rd(FLASH_ACR);
  v38 = rd(SRAM0 + 4u * V_TIM17);
  v46 = rd(SRAM0 + 4u * V_CTX);
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x28, 0);             /* PSC */
  wr(TIM17 + 0x2C, T_PER - 1u);    /* ARR */
  wr(TIM17 + 0x14, 1);             /* UG */
  wr(TIM17 + 0x10, 0);
  wr(TIM17 + 0x0C, 1u);            /* UIE */
  f->n0 = 3;
  f->run = 1;
  wr(SRAM0 + 4u * V_CTX, (uint32_t)f);
  wr(SRAM0 + 4u * V_TIM17, (uint32_t)&tim_isr | 1u);
  wr(SYSCFG_CFGR1, (cfgr1 & ~3u) | 3u);
  wr(NVIC_ICPR, 1u << IRQ_TIM17);
  wr(NVIC_ISER, 1u << IRQ_TIM17);
  wr(TIM17 + 0x00, 1);
  t0 = api->ms();
  api->delay_us(20000);
  if (!f->pw) er = -11;
  pr = pw0 = f->pw, ri = f->wi;
  pr -= pr % BLK, ri -= ri % BLK;
  while (!er) {
    now = api->ms();
    if (api->key_poll()) er = -6;
    if (now - t0 >= REC_MS || n + 1 >= cap) break;
    if (now - dt >= 1000) { /* each second: the PA still off */
      dt = now;
      if (api->fd_read(0x36) & 0x0080u) er = -5;
    }
    if (take(f, &pr, &ri, &lost, x)) {
      int32_t pk = 0, v;
      for (i = 0; i < BLK; i++) {
        v = x[i] < 0 ? -x[i] : x[i];
        if (v > pk) pk = v;
      }
      if ((pk * g) >> 8 > AGC_TOP) g = (AGC_TOP << 8) / (pk + 1); /* down at once */
      else if (g < AGC_MAX) g += (g >> 6) + 1;                 /* up 2.4 dB a second */
      if (g < AGC_MIN) g = AGC_MIN;
      for (i = 0; i < BLK; i++) x[i] = (int16_t)sat16((x[i] * g) >> 8);
      if (slim_encode10(e, x, sf + SLIM_BYTES * n)) n++;
    }
  }
  /* the interrupt off, the map and the vector back */
  dt = api->ms() - t0;
  f->run = 0;
  pkm = f->pk;
  wr(NVIC_ICER, 1u << IRQ_TIM17);
  wr(TIM17 + 0x00, 0);
  wr(TIM17 + 0x0C, 0);
  wr(NVIC_ICPR, 1u << IRQ_TIM17);
  wr(SYSCFG_CFGR1, cfgr1);
  wr(FLASH_ACR, acr);
  wr(SRAM0 + 4u * V_TIM17, v38);
  wr(RCC_APB2ENR, apb2);
  while (n < cap && slim_flush(e, sf + SLIM_BYTES * n)) n++; /* (the context entry still ours) */
  wr(SRAM0 + 4u * V_CTX, v46);
keyed:
  if (s30 && api->fd_read(0x30) != s30 && api->fd_write(0x30, (uint16_t)s30) != 0) mic_bus_write(api, 0x30, s30);
  api->fd_write(0x47, r47);
  api->fd_write(0x48, r48);
  api->fd_write(0x7D, r7d);
  api->tx_off();
#if MS_CUE
  if (!er && sound(api, 1, wide) == 0) call(api); /* the voice's carrier gone, the last ID */
#else
  if (!er) call(api);
#endif
back:
  api->tx_off();
  api->tune(home, wide);
  wr(PA11_GPIOA + PA11_MODER, moder);
  wr(PA11_GPIOA + PA11_OSPEEDR, ospeed);
  wr(PA11_GPIOA + PA11_BSRR, odr ? 1u << PA11_PIN : 1u << (PA11_PIN + 16));
  if (amp) wr(MIC_GPIOB + MIC_BSRR, AMP);
  if (ret != -7) {
    co_show(api, "MIC SOTTO CABLE OUT", "PLUG CABLE IN", "THEN ANY KEY");
    co_key(api, KEY_WAIT_MS);
    co_show(api, "MIC SOTTO", "SENDING", "");
    api->delay_us(1000000);
  }
  if (dt) {
    p = dec(put(line, "mso ms="), dt);
    p = kv(p, " sps=", (f->pw - pw0) * 1000u / dt);
    p = kv(p, " pk=", pkm);
    p = hex(put(p, " g="), (uint32_t)g, 4);
    p = kv(p, " isr=", f->isr / (dt * 960u));
    p = kv(p, " rd=", f->rdc / (dt * 960u));
    *p = 0;
    api->say(line);
  }
  for (i = 0; i < n; i += 4) {
    say_frames(api, line, sf, i, n - i < 4 ? n - i : 4);
    api->delay_us(15000);
  }
  p = kv(put(line, "micsotto end"), " n=", n);
  p = kv(p, " lost=", lost);
  p = put(p, " e=");
  if (er < 0) *p++ = '-';
  p = dec(p, (uint32_t)(er < 0 ? -er : er));
  *p = 0;
  api->say(line);
  if (n && api->level >= 11) api->sotto_play(sf, n, KM_SOTTO_1200, 0, "K6 MIC");
  return ret ? ret : er ? er : (int)n;
}
