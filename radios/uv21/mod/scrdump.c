/* scrdump: reads the screen back from the ST7735S's own memory and prints it on the console, so
   the bench can see what the UV-21 Pro shows without a camera. MIT

   The panel's SDA (PB15) is one data line both ways: the port drives it as SPI2's MOSI, and the
   controller answers on it when asked to read (the bring-up image read RDDID 7C 89 F0 this way).
   SPI2 can't receive on its MOSI pin, so this module takes PB13 (SCK) and PB15 back as plain
   GPIO, clocks the reads itself at ~3 MHz (the ST7735S reads at 6.6 MHz at most) and gives both
   pins back to SPI2 before it returns. CS (PB12) and D/C (PC15) are already GPIO.

   SURF has left while a module runs and nothing draws, so the panel still holds SURF's last frame;
   SURF redraws all of it when the module returns.

   It prints the screen in c/common/scrgrab.h's format (RGB565 rows, 8-row bands packed with
   LZ4: about 3 KB for SURF's 40 KB), after "scr id XXXXXXXX", RDDID as clocked (the 24-bit ID after
   a dummy bit). tools/screen_grab.py turns it into a PNG and checks its CRC. -D SD_RAW=1 prints
   the raw bytes clocked after each row's RAMRD instead ("scr r YY KK HEX", 24 bytes a line), for
   finding the read's framing again. -D SD_Y0 / SD_ROWS pick the rows (default the whole 160).

   The ST7735S answers RAMRD with a dummy clock and a dummy byte, then each pixel as three bytes,
   its 6-bit R, G, B in their top bits (measured on the radio 2026-11-28). */
#include "uv21_mod.h"
#ifndef SD_RAW
#include "../../../c/common/scrgrab.c"
#endif

#ifndef SD_Y0
#define SD_Y0 0
#endif
#ifndef SD_ROWS
#define SD_ROWS 160
#endif
#define SD_W 128
#define SD_BYTES (SD_W * 3 + 4)   /* 18-bit pixels read as 3 bytes, and slack for dummy bits */
#define SD_DUMMY 9                /* bits before the first pixel */
#ifndef SD_BAND
#define SD_BAND 8                 /* rows an LZ4 block (RAM: 4 KB a band of 8, packed and not) */
#endif
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

/* half a clock: 20 cycles, ~170 ns at 120 MHz and ~330 ns at 60, so reads stay well under the
   controller's 6.6 MHz at either clock (10 cycles read at ~3 MHz on the radio at 60 MHz; at 120 MHz
   they would have reached ~6 MHz, too close: blowfang opt/opt-clk review, 2026-11-28) */
static inline void half(void) {
  __asm__ volatile("nop\nnop\nnop\nnop\nnop\nnop\nnop\nnop\nnop\nnop"
                   "\nnop\nnop\nnop\nnop\nnop\nnop\nnop\nnop\nnop\nnop");
}

__attribute__((noinline)) static void put_byte(uint32_t b) {
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

#ifndef SD_RAW
/* bit `bit` of the raw read, counting from its first bit */
static inline uint32_t sd_bit(const uint8_t *raw, unsigned bit) { return (raw[bit >> 3] >> (7 - (bit & 7))) & 1u; }

/* the 6 top bits of the 8 starting at bit `bit` */
static inline uint32_t sd_six(const uint8_t *raw, unsigned bit) {
  uint32_t v = 0;
  for (int i = 0; i < 6; i++) v = v << 1 | sd_bit(raw, bit + (unsigned)i);
  return v;
}
#endif

/* SPI2's pins as they were, while the module clocks SCK and SDA itself */
typedef struct sd_pins {
  uint32_t cfgr, pull;
} sd_pins_t;

static void sd_take(sd_pins_t *k) {
  while (REG(SPI2_STS) & SPI_BF) {}          /* the last SPI byte out */
  k->cfgr = REG(GPIOB + G_CFGR);
  k->pull = REG(GPIOB + G_PULL);
  hi(GPIOB, CS);
  lo(GPIOB, SCK);
  field(GPIOB, G_CFGR, SCK, 1);              /* SCK and SDA: GPIO outputs while we clock */
  field(GPIOB, G_CFGR, SDA, 1);
}

static void sd_give(const sd_pins_t *k) {
  REG(GPIOB + G_PULL) = k->pull;              /* SPI2 gets its pins back as they were */
  REG(GPIOB + G_CFGR) = k->cfgr;
  hi(GPIOB, CS);
}

/* one panel row's raw RAMRD bytes (SD_BYTES) into raw */
static void sd_read_raw(int y, uint8_t *raw) {
  const uint8_t ca[4] = {0, 0, 0, SD_W - 1};
  const uint8_t ra[4] = {0, (uint8_t)y, 0, (uint8_t)y};
  cmd(0x2A, ca, 4);                           /* CASET: the whole row */
  cmd(0x2B, ra, 4);                           /* RASET: this row */
  read_cmd(0x2E, raw, SD_BYTES);              /* RAMRD */
}

#ifndef SD_RAW
/* one panel row as RGB565, big-endian (2 * SD_W bytes into rgb); raw is SD_BYTES of scratch */
static void sd_read_row(int y, uint8_t *raw, uint8_t *rgb) {
  sd_read_raw(y, raw);
  for (unsigned x = 0; x < SD_W; x++) {
    unsigned b0 = SD_DUMMY + 24 * x;
    uint32_t c = (sd_six(raw, b0) >> 1) << 11 | sd_six(raw, b0 + 8) << 5 | sd_six(raw, b0 + 16) >> 1;
    rgb[2 * x] = (uint8_t)(c >> 8);
    rgb[2 * x + 1] = (uint8_t)c;
  }
}
#endif

/* the entry; another module may include this file with -D SD_ENTRY=its_name to grab the screen
   from its own code (radios/uv21/mod/clk120.c) */
#ifndef SD_ENTRY
#define SD_ENTRY um_main
#endif

int SD_ENTRY(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  char line[16 + 2 * SD_PIECE];       /* (60 characters) */
  uint8_t *raw = ram;                 /* SD_BYTES of the arena's zeroed RAM */
  sd_pins_t pins;
#ifndef SD_RAW
  uint8_t *rgb = raw + ((SD_BYTES + 3) & ~3u);              /* one RGB565 row */
  uint8_t *band = rgb + 2 * SD_W;
  uint8_t *pack = band + SD_BAND * 2 * SD_W;
  uint16_t *tab = (uint16_t *)(pack + SD_BAND * 2 * SD_W);
  scr_t s;
  if (ram_len < (uint32_t)((uint8_t *)(tab + LZ4C_TAB) - ram)) return -1;
  s.say = api->say;
  s.band = band;
  s.pack = pack;
  s.tab = tab;
#else
  if (ram_len < SD_BYTES) return -1;
#endif

  sd_take(&pins);
  read_cmd(0x04, raw, 4);                     /* RDDID */
  char *p = um_put(line, "scr id ");
  for (int i = 0; i < 4; i++) p = um_hex(p, raw[i], 2);
  api->say(line);

#ifndef SD_RAW
  scr_begin(&s, SD_W, SD_ROWS, SCR_RGB565, 2 * SD_W, SD_BAND);
#endif
  for (int y = SD_Y0; y < SD_Y0 + SD_ROWS && y < 160; y++) {
#ifndef SD_RAW
    sd_read_row(y, raw, rgb);
    scr_row(&s, rgb);
#else
    sd_read_raw(y, raw);
    for (int k = 0; k * SD_PIECE < SD_BYTES; k++) {
      p = um_put(line, "scr r ");
      p = um_hex(p, (uint32_t)y, 2);
      *p++ = ' ';
      p = um_hex(p, (uint32_t)k, 2);
      *p++ = ' ';
      for (int i = k * SD_PIECE; i < SD_BYTES && i < (k + 1) * SD_PIECE; i++) p = um_hex(p, raw[i], 2);
      api->say(line);
    }
#endif
  }
#ifndef SD_RAW
  scr_end(&s);
#endif
  sd_give(&pins);
  return SD_ROWS;
}
