/* scrdump: reads the screen back from the ST7735S's own memory and prints it on the console, so
   the bench can see what the UV-21 Pro shows without a camera. MIT

   The panel's SDA (PB15) is one data line both ways: the port drives it as SPI2's MOSI, and the
   controller answers on it when asked to read (the bring-up image read RDDID 7C 89 F0 this way).
   SPI2 can't receive on its MOSI pin, so this module takes PB13 (SCK) and PB15 back as plain
   GPIO, clocks the reads itself at ~3 MHz (the ST7735S reads at 6.6 MHz at most) and gives both
   pins back to SPI2 before it returns. CS (PB12) and D/C (PC15) are already GPIO.

   SURF has left while a module runs and nothing draws, so the panel still holds SURF's last frame;
   SURF redraws all of it when the module returns.

   Lines:  scr id XXXXXXXX                RDDID, 32 bits as clocked (the 24-bit ID after a dummy bit)
           scr r YY KK HEX...             row YY (hex), piece KK: the raw bytes clocked after RAMRD
                                          (SD_BYTES a row, in pieces of SD_PIECE), dummy bits and all
   tools/uv21_screen.py turns them into a PNG: it finds the bit alignment itself.
   -D SD_Y0 / SD_ROWS pick the rows (default the whole 160). */
#include "uv21_mod.h"

#ifndef SD_Y0
#define SD_Y0 0
#endif
#ifndef SD_ROWS
#define SD_ROWS 160
#endif
#define SD_W 128
#define SD_BYTES (SD_W * 3 + 4)   /* 18-bit pixels read as 3 bytes, and slack for dummy bits */
#define SD_PIECE 24                 /* the port's say() prints 64 characters of a line at most */

#define REG(a) (*(volatile uint32_t *)(a))
#define GPIOB 0x48000400u
#define GPIOC 0x48000800u
#define G_CFGR 0x00u                /* 2 bits a pin: 00 in, 01 out, 10 alternate (AT32F421) */
#define G_PULL 0x0Cu                /* 01 up */
#define G_IDT 0x10u
#define G_SCR 0x18u
#define G_CLR 0x28u
#define SPI2_STS 0x40003808u
#define SPI_BF (1u << 7)

#define CS 12
#define SCK 13
#define SDA 15
#define DC 15                       /* on port C */

static inline void hi(uint32_t port, int pin) { REG(port + G_SCR) = 1u << pin; }
static inline void lo(uint32_t port, int pin) { REG(port + G_CLR) = 1u << pin; }
static inline void field(uint32_t port, uint32_t off, int pin, uint32_t v) {
  REG(port + off) = (REG(port + off) & ~(3u << (2 * pin))) | (v << (2 * pin));
}

/* ~80 ns at 60 MHz: half a clock, so reads stay under the controller's 6.6 MHz */
static inline void half(void) {
  __asm__ volatile("nop\nnop\nnop\nnop\nnop\nnop\nnop\nnop\nnop\nnop");
}

static void put_byte(uint32_t b) {
  for (int i = 7; i >= 0; i--) {
    lo(GPIOB, SCK);
    if ((b >> i) & 1) hi(GPIOB, SDA); else lo(GPIOB, SDA);
    half();
    hi(GPIOB, SCK);                 /* the controller samples on the rising edge */
    half();
  }
  lo(GPIOB, SCK);
}

static void cmd(uint32_t c, const uint8_t *args, int n) {
  lo(GPIOC, DC);
  lo(GPIOB, CS);
  put_byte(c);
  hi(GPIOC, DC);
  for (int i = 0; i < n; i++) put_byte(args[i]);
  hi(GPIOB, CS);
}

/* a command, then n bytes clocked back on SDA (CS stays low through both) */
static void read_cmd(uint32_t c, uint8_t *out, int n) {
  lo(GPIOC, DC);
  lo(GPIOB, CS);
  put_byte(c);
  hi(GPIOC, DC);
  field(GPIOB, G_PULL, SDA, 1);     /* nobody answering reads as ones */
  field(GPIOB, G_CFGR, SDA, 0);
  for (int k = 0; k < n; k++) {
    uint32_t v = 0;
    for (int i = 0; i < 8; i++) {
      hi(GPIOB, SCK);
      half();
      v = v << 1 | ((REG(GPIOB + G_IDT) >> SDA) & 1u);
      lo(GPIOB, SCK);
      half();
    }
    out[k] = (uint8_t)v;
  }
  hi(GPIOB, CS);
  field(GPIOB, G_CFGR, SDA, 1);     /* (driven again before the next command) */
  field(GPIOB, G_PULL, SDA, 0);
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  char line[16 + 2 * SD_PIECE];       /* (60 characters) */
  uint8_t *row = ram;               /* SD_BYTES of the arena's zeroed RAM */
  uint32_t cfgr, pull;
  if (ram_len < SD_BYTES) return -1;

  while (REG(SPI2_STS) & SPI_BF) {}          /* the last SPI byte out */
  cfgr = REG(GPIOB + G_CFGR);
  pull = REG(GPIOB + G_PULL);
  hi(GPIOB, CS);
  lo(GPIOB, SCK);
  field(GPIOB, G_CFGR, SCK, 1);              /* SCK and SDA: GPIO outputs while we clock */
  field(GPIOB, G_CFGR, SDA, 1);

  read_cmd(0x04, row, 4);                     /* RDDID */
  char *p = um_put(line, "scr id ");
  for (int i = 0; i < 4; i++) p = um_hex(p, row[i], 2);
  api->say(line);

  for (int y = SD_Y0; y < SD_Y0 + SD_ROWS && y < 160; y++) {
    const uint8_t ca[4] = {0, 0, 0, SD_W - 1};
    const uint8_t ra[4] = {0, (uint8_t)y, 0, (uint8_t)y};
    cmd(0x2A, ca, 4);                         /* CASET: the whole row */
    cmd(0x2B, ra, 4);                         /* RASET: this row */
    read_cmd(0x2E, row, SD_BYTES);            /* RAMRD */
    for (int k = 0; k * SD_PIECE < SD_BYTES; k++) {
      p = um_put(line, "scr r ");
      p = um_hex(p, (uint32_t)y, 2);
      *p++ = ' ';
      p = um_hex(p, (uint32_t)k, 2);
      *p++ = ' ';
      for (int i = k * SD_PIECE; i < SD_BYTES && i < (k + 1) * SD_PIECE; i++) p = um_hex(p, row[i], 2);
      api->say(line);
    }
  }

  REG(GPIOB + G_PULL) = pull;                 /* SPI2 gets its pins back as they were */
  REG(GPIOB + G_CFGR) = cfgr;
  hi(GPIOB, CS);
  return SD_ROWS;
}
