/* stockfont: are Baofeng's stock fonts still in this K6's SPI flash, and can we draw them?

   The stock K6 firmware has no font in the MCU's flash and the LCD (SC5260) has no character
   ROM: it reads every glyph from the 2 MB SPI flash (XM25QH16C). Baofeng's published source
   (github.com/cnt7/BAOFENG-UV-K6-Firmware, src/Driver/FlashFont.c; BFPL-1.0, read for the
   addresses only, no code taken) puts them at:

     0x04B7C0  8x16 ASCII, 16 B a glyph (8 columns of the top page, then 8 of the bottom page)
     0x04BFC0  5x7 ASCII, 8 B a glyph (5 columns used)
     0x076D40  6x12 ASCII, 12 B a glyph (6 + 6)
     0x010000  GB2312 16x16, 32 B a glyph;  0x04CF80  GB2312 12x12, 24 B a glyph

   ASCII glyph = char - 0x20. A column is one byte, bit 0 the top pixel (the LCD's page format).
   Jeff's pre-Aura dump has all five; Aura's first-boot format erases only its own sectors.
   The module API's fl_read starts at 0x0F0000, so this module reads the SPI flash itself, with
   READ (0x03) and READ ID (0x9F) only: it never sends a write, erase or status-write command.
   It uses SPI1 as Aura left it (mode 3, soft NSS on PA2) and masks interrupts for each short
   transaction (at most 256 bytes, ~0.4 ms), so nothing else can talk to the flash mid-read.

   It says: the flash's JEDEC ID, a zlib CRC-32 of each font, and a map of the 32 64 KB blocks
   ('.' all 0xFF, '#' data). Then it draws two screens from the stock glyphs, 8 s each, twice
   (any key ends it): 8x16 and 5x7 text, then 6x12 text and two GB2312 characters at 16x16 and
   12x12. Returns 0 if the three ASCII fonts hold data, 1 if any is blank, -2 if SPI1 isn't set
   up as expected, -3 below API level 6, -4 if the SPI bus stopped answering.
   SPDX-License-Identifier: MIT */
#define K6_MODULE_LEVEL 6
#include "../k6_mod.h"
#ifndef K6_NAME
#define K6_NAME "STOCK FONT"   /* its name in the MENU list (a file that includes this one may set its own) */
#endif
K6_MODULE_NAME(K6_NAME);

#define SPI1 0x40013000u
#define SPI_CR1 0x00u
#define SPI_SR 0x08u
#define SPI_DR 0x0Cu
#define SR_RXNE 0x01u
#define SR_TXE 0x02u
#define SR_BSY 0x80u
#define CR1_SPE 0x40u
#define CR1_MSTR 0x04u
#define GPIOA 0x48000000u
#define GPIO_BSRR 0x18u
#define GPIO_BRR 0x28u
#define CS_PIN (1u << 2)            /* PA2: the SPI flash's chip select */

#define R16(a) (*(volatile uint16_t *)(uintptr_t)(a))
#define R8(a) (*(volatile uint8_t *)(uintptr_t)(a))
#define W32(a, v) (*(volatile uint32_t *)(uintptr_t)(a) = (v))

#define F8X16 0x04B7C0u
#define F5X7 0x04BFC0u
#define F6X12 0x076D40u
#define GB16 0x010000u
#define GB12 0x04CF80u
#define GB16_LEN (F8X16 - GB16)     /* up to the ASCII fonts: 243,648 B */
#define GB12_LEN (F6X12 - GB12)     /* 171,456 B */
#define SPIN 200000u                /* a status wait gives up after this many reads */
#define CHUNK 256u                  /* bytes a transaction */
#define ROWS KM_WF_ROWS

typedef struct {
  const k6_api_t *api;
  uint8_t *wf;                      /* ROWS x 16 bytes, from the scratch */
  uint8_t *buf;                     /* CHUNK bytes, from the scratch (the stack is ~700 B) */
  int dead;                         /* the bus stopped answering */
  char ln[64];
} sf_t;

static void irq_off(void) { __asm__ volatile("cpsid i" ::: "memory"); }
static void irq_on(void) { __asm__ volatile("cpsie i" ::: "memory"); }

static int wait_sr(sf_t *s, uint16_t mask, uint16_t want) {
  uint32_t n;
  for (n = 0; n < SPIN; n++)
    if ((R16(SPI1 + SPI_SR) & mask) == want) return 0;
  s->dead = 1;
  return -1;
}

static uint8_t xfer(sf_t *s, uint8_t b) {
  uint8_t r;
  if (s->dead || wait_sr(s, SR_TXE, SR_TXE)) return 0xFF;
  R8(SPI1 + SPI_DR) = b;            /* byte access: a 16-bit write would send two bytes */
  if (wait_sr(s, SR_RXNE, SR_RXNE)) return 0xFF;
  r = R8(SPI1 + SPI_DR);
  wait_sr(s, SR_BSY, 0);
  return r;
}

/* one transaction: CS low, cmd (+ 24-bit address if addr_bytes), n bytes in, CS high */
static void txn(sf_t *s, uint8_t cmd, uint32_t addr, int addr_bytes, uint8_t *buf, uint32_t n) {
  uint32_t i;
  irq_off();
  wait_sr(s, SR_BSY, 0);
  for (i = 0; i < 8 && (R16(SPI1 + SPI_SR) & SR_RXNE); i++) (void)R8(SPI1 + SPI_DR);
  W32(GPIOA + GPIO_BRR, CS_PIN);
  xfer(s, cmd);
  if (addr_bytes) {
    xfer(s, (uint8_t)(addr >> 16));
    xfer(s, (uint8_t)(addr >> 8));
    xfer(s, (uint8_t)addr);
  }
  for (i = 0; i < n; i++) buf[i] = xfer(s, 0);
  W32(GPIOA + GPIO_BSRR, CS_PIN);
  irq_on();
}

static void rd(sf_t *s, uint32_t addr, uint8_t *buf, uint32_t n) {
  while (n) {
    uint32_t k = n < CHUNK ? n : CHUNK;
    txn(s, 0x03, addr, 1, buf, k);
    addr += k, buf += k, n -= k;
  }
}

/* zlib's CRC-32 of n bytes from addr; *ff gets 1 if every byte was 0xFF */
static uint32_t crc_of(sf_t *s, uint32_t addr, uint32_t n, int *ff) {
  uint8_t *b = s->buf;
  uint32_t c = 0xFFFFFFFFu, i, k;
  int j, all = 1;
  while (n && !s->dead) {
    k = n < CHUNK ? n : CHUNK;
    rd(s, addr, b, k);
    for (i = 0; i < k; i++) {
      all &= b[i] == 0xFF;
      c ^= b[i];
      for (j = 0; j < 8; j++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
    }
    addr += k, n -= k;
    (void)s->api->ms();             /* keeps the module clock running over a long read */
  }
  *ff = all;
  return ~c;
}

static char *put(char *p, const char *t) {
  while (*t) *p++ = *t++;
  return p;
}

static char *hex(char *p, uint32_t v, int digits) {
  const char *x = "0123456789abcdef";
  while (digits--) *p++ = x[(v >> (4 * digits)) & 15];
  return p;
}

/* ---- drawing stock glyphs into the waterfall rows (16 bytes a row, bit 7 of byte 0 leftmost) */

static void px(sf_t *s, int x, int y) {
  if (x >= 0 && x < 128 && y >= 0 && y < ROWS) s->wf[(y << 4) + (x >> 3)] |= (uint8_t)(0x80 >> (x & 7));
}

/* a glyph of w columns and h rows in page format: page p's w bytes, then page p + 1's */
static void glyph(sf_t *s, int x0, int y0, int w, int h, const uint8_t *g) {
  int p, c, r;
  for (p = 0; p * 8 < h; p++)
    for (c = 0; c < w; c++)
      for (r = 0; r < 8 && p * 8 + r < h; r++)
        if (g[p * w + c] >> r & 1) px(s, x0 + c, y0 + p * 8 + r);
}

enum { A5X7, A6X12, A8X16 };

static int ascii(sf_t *s, int font, int x, int y, const char *t) {
  uint8_t g[16];
  uint32_t n;
  for (; *t; t++) {
    n = (uint32_t)(*t < 0x20 || *t > 0x7E ? ' ' : *t) - 0x20;
    if (font == A5X7) {
      rd(s, F5X7 + (n << 3), g, 5);
      glyph(s, x, y, 5, 7, g);
      x += 6;
    } else if (font == A6X12) {
      rd(s, F6X12 + (n << 3) + (n << 2), g, 12);
      glyph(s, x, y, 6, 12, g);
      x += 6;
    } else {
      rd(s, F8X16 + (n << 4), g, 16);
      glyph(s, x, y, 8, 16, g);
      x += 8;
    }
  }
  return x;
}

/* one GB2312 character (two bytes, the B0-F7 rows: the hanzi) at 16x16 or 12x12 */
static int hanzi(sf_t *s, int big, int x, int y, uint8_t hi, uint8_t lo) {
  uint8_t g[32];
  uint32_t i = (uint32_t)(hi - 0xB0) * 94u + (uint32_t)(lo - 0xA1);
  if (big) {
    rd(s, GB16 + ((i + 846u) << 5), g, 32);
    glyph(s, x, y, 16, 16, g);
    return x + 16;
  }
  i += 376u;
  rd(s, GB12 + (i << 4) + (i << 3), g, 24);
  glyph(s, x, y, 12, 12, g);
  return x + 12;
}

static void clear(sf_t *s) {
  int i;
  for (i = 0; i < ROWS * 16; i++) s->wf[i] = 0;
}

static void screen(sf_t *s, int page, const char *foot) {
  const k6_api_t *api = s->api;
  int x;
  clear(s);
  if (page == 0) {
    api->lcd_text(0, "STOCK FONTS 1/2");
    api->lcd_text(1, "SPI 8x16 and 5x7:");
    ascii(s, A8X16, 0, 0, "AG7EW 8x16 Ok!");
    ascii(s, A5X7, 0, 19, "5x7 The quick fox 0-9");
  } else {
    api->lcd_text(0, "STOCK FONTS 2/2");
    api->lcd_text(1, "SPI 6x12, GB2312:");
    ascii(s, A6X12, 0, 0, "6x12 Lynden AG7EW 73");
    x = hanzi(s, 1, 0, 13, 0xB1, 0xA6);          /* bao: Baofeng's first character */
    x = hanzi(s, 1, x, 13, 0xB7, 0xE6);          /* feng */
    x = hanzi(s, 0, x + 8, 15, 0xB6, 0xCC);      /* duan */
    x = hanzi(s, 0, x, 15, 0xB2, 0xA8);          /* bo: "shortwave", 12x12 */
    ascii(s, A5X7, x + 6, 18, "16 + 12");
  }
  api->lcd_wf(s->wf, ROWS, 0);
  api->lcd_text(5, foot);
  api->lcd_flush();
}

int k6_main(const k6_api_t *api) {
  sf_t st, *s = &st;
  uint8_t id[3];
  uint32_t c8, c5, c6, g16, g12, n, blk, t0;
  int f8, f5, f6, fg16, fg12, page, round;
  char foot[24], *p;
  uint16_t cr1;
  if (api->level < 6) return -3;
  s->api = api;
  s->dead = 0;
  s->wf = (uint8_t *)api->scratch(&n);
  if (n < ROWS * 16 + CHUNK) return -3;
  s->buf = s->wf + ROWS * 16;

  cr1 = R16(SPI1 + SPI_CR1);
  p = put(s->ln, "spi1 cr1=");
  p = hex(p, cr1, 4);
  if ((cr1 & (CR1_SPE | CR1_MSTR)) != (CR1_SPE | CR1_MSTR)) {
    p = put(p, " not set up: stop");
    *p = 0;
    api->say(s->ln);
    return -2;
  }
  txn(s, 0x9F, 0, 0, id, 3);
  p = put(p, " jedec=");
  p = hex(p, (uint32_t)id[0] << 16 | (uint32_t)id[1] << 8 | id[2], 6);
  *p = 0;
  api->say(s->ln);
  if (s->dead) {
    api->say("spi bus not answering: stop");
    return -4;
  }

  c8 = crc_of(s, F8X16, 95u * 16u, &f8);
  c5 = crc_of(s, F5X7, 95u * 8u, &f5);
  c6 = crc_of(s, F6X12, 95u * 12u, &f6);
  p = put(s->ln, "crc 8x16 ");
  p = hex(p, c8, 8);
  p = put(p, f8 ? "FF 5x7 " : " 5x7 ");
  p = hex(p, c5, 8);
  p = put(p, f5 ? "FF 6x12 " : " 6x12 ");
  p = hex(p, c6, 8);
  p = put(p, f6 ? "FF" : "");
  *p = 0;
  api->say(s->ln);

  g16 = crc_of(s, GB16, GB16_LEN, &fg16);
  g12 = crc_of(s, GB12, GB12_LEN, &fg12);
  p = put(s->ln, "crc gb16 ");
  p = hex(p, g16, 8);
  p = put(p, fg16 ? "FF gb12 " : " gb12 ");
  p = hex(p, g12, 8);
  p = put(p, fg12 ? "FF" : "");
  *p = 0;
  api->say(s->ln);

  /* the block map: '.' for a 64 KB block of all 0xFF, '#' for data */
  p = put(s->ln, "map ");
  for (blk = 0; blk < 32 && !s->dead; blk++) {
    uint8_t *b = s->buf;
    uint32_t a, i;
    int all = 1;
    for (a = blk << 16; a < (blk + 1) << 16 && all; a += CHUNK) {
      rd(s, a, b, CHUNK);
      for (i = 0; i < CHUNK; i++) all &= b[i] == 0xFF;
    }
    *p++ = all ? '.' : '#';
    (void)api->ms();
  }
  *p = 0;
  api->say(s->ln);
  if (s->dead) {
    api->say("spi bus stopped answering: stop");
    return -4;
  }

  p = put(foot, (f8 | f5 | f6) ? "ASCII BLANK " : "fonts here ");
  p = hex(p, c5 >> 16, 4);
  *p = 0;
  for (round = 0; round < 2; round++)
    for (page = 0; page < 2; page++) {
      screen(s, page, foot);
      for (t0 = api->ms(); api->ms() - t0 < 8000;) {
        if (api->key_down()) {
          api->say(s->dead ? "drawn, bus failed mid-way" : "drawn, key: done");
          return s->dead ? -4 : (f8 | f5 | f6) ? 1 : 0;
        }
        api->delay_us(20000);
      }
    }
  api->say(s->dead ? "drawn, bus failed mid-way" : "drawn: done");
  return s->dead ? -4 : (f8 | f5 | f6) ? 1 : 0;
}
