/* clk120: moves the UV-21 Pro's MCU clock from RAM, checks the radio at the new clock, and puts
   the old one back, so blowfang opt/opt-clk (SCLK 60 -> 120 MHz) is tried before any flash (Jeff,
   2026-11-28: "develop a safety plan. If possible build a ram module that can test this. Combine
   with screenshot api tests"). On the 60 MHz image it goes to 4 MHz x CK_MULT (30: 120 MHz) and
   back; on a 120 MHz image, CK_MULT=15 goes down to 60 MHz and back (the way home). MIT

   The image's numbers stay as built (its SCLK_HZ), so while the module holds the new clock it sets
   the peripherals the port derives from the clock for it, and puts the port's own values back:
     USART1 BAUDR   SCLK / 115,200 (60 MHz: 521, 120 MHz: 1042)
     SPI2 (panel)   the fastest MDIV at 15 MHz or under (60 MHz: /4, 120 MHz: /8), as the port's
     TMR14 (ticks)  DIV for 100 us (60 MHz: 5999, 120 MHz: 11999), its count kept
     ADC            ADCDIV for 28 MHz or under (120 MHz: /6 = 20 MHz; the 60 MHz port leaves /2)
   SysTick waits (api->delay_us, the RF chip's bit delays) follow the image's SCLK_HZ, so at twice
   the clock they are half as long: the module waits with api->ticks() instead, and its RF reads at
   120 MHz run with half the bit time the 120 MHz image would use (a harder test than the image's).

   The switch is the branch's (and Artery's BSP's): SCLK to HICK, PLL off, PLLMULT and ADCDIV,
   PLL on and locked, auto step on, SCLK to PLL, auto step off, interrupts off throughout. FLASH_PSR
   must already hold 3 wait states (both images write 0x153), or the module refuses, as it refuses
   any clock setup it doesn't know. Every wait is bounded: a PLL that doesn't lock in time sends it
   back to the clock it came from.

   The deadman: before the first switch the module starts the independent watchdog (WDT, on the
   ~40 kHz LICK, ~26 s) and feeds it while it runs. The port never feeds it, so ~26 s after the
   module returns the radio resets and boots its own image afresh. A hang at the new clock (a
   fault, a lost lock) resets the radio the same way, with no hands. CK_DEADMAN=0 leaves it off.

   Checks, at the image's clock ("home"), at the new one ("new"), and some again back home ("back"):
     clk rf    ~30 RF-chip setup registers, read twice at home (those that agree), then compared
     clk fl    CRC-32 of the 60 KB app area of the MCU's flash (the wait states)
     clk cpu   a RAM loop and a call loop into flash, in 100 us ticks (half at twice the clock)
     clk adc   the battery channel, 16 conversions averaged (information only)
     clk rssi  dBm on a few frequencies, clock harmonics among them (information only)
     clk pat   a test card drawn through SPI2 at the new clock, read back with RAMRD; bad pixels
     scr ...   screens as tools/screen_grab.py reads them, each after a "clk grab TAG" line: SURF's
               last frame at home and new (they must match), then the test card (CK_GRAB=0: none)
     clk soak  CK_SOAK_S seconds at the new clock of flash CRCs and RF reads; mismatches
   It returns 0 when every check passes, else the F_ bits below. radios/uv21/mod/clk120.py reads the log.

   Settings: CK_MULT (30; 15..30, not the image's own), CK_SOAK_S (10), CK_DEADMAN (1), CK_GRAB (1),
   CK_RSSI (1), CK_FL_LEN (0xF000); scrdump.c's SD_Y0 / SD_ROWS pick the rows the grabs and the
   card's read-back cover (the emulator test reads a few). */
#include "uv21_mod.h"
#define SD_ENTRY sd_grab
#define SD_BAND 4                       /* 2 KB of band buffers: the module is big */
#include "scrdump.c"

#ifndef CK_MULT
#define CK_MULT 30
#endif
#ifndef CK_SOAK_S
#define CK_SOAK_S 10
#endif
#ifndef CK_DEADMAN
#define CK_DEADMAN 1
#endif
#ifndef CK_GRAB
#define CK_GRAB 1
#endif
#ifndef CK_RSSI
#define CK_RSSI 1
#endif
#if CK_MULT < 15 || CK_MULT > 30
#error "CK_MULT: 15..30 (60..120 MHz; the AT32F421 tops out at 120)"
#endif

/* the return value's bits */
#define F_REFUSED 0x001                 /* not the clock the module expects to start from */
#define F_LOCK 0x002                    /* the PLL didn't lock at CK_MULT: it went back */
#define F_RF 0x004                      /* an RF register read differently */
#define F_FLASH 0x008                   /* the flash CRC changed */
#define F_PAT_NEW 0x010                 /* the test card read back wrong at the new clock */
#define F_PAT_BACK 0x020                /* ... back home */
#define F_SOAK 0x040                    /* a mismatch in the soak */
#define F_BACK 0x080                    /* the way back failed (then the deadman resets) */
#define F_STATE 0x100                   /* a register didn't read back as written */

#define CRM 0x40021000u
#define CRM_CTRL (CRM + 0x00)
#define CRM_CFG (CRM + 0x04)
#define CRM_PLL (CRM + 0x2C)
#define CRM_MISC2 (CRM + 0x54)
#define HICKSTBL (1u << 1)
#define PLLEN (1u << 24)
#define PLLSTBL (1u << 25)
#define CFG_MULT_MASK (0xFu << 18 | 3u << 29)
#define CFG_ADC_MASK (3u << 14 | 1u << 28)
#define FLASH_PSR 0x40022000u
#define USART1_STS 0x40013800u
#define USART1_BAUDR 0x40013808u
#define TDC (1u << 6)
#define SPI2_CTRL1 0x40003800u
#define SPI2_DT 0x4000380Cu
#define SPI_TDBE (1u << 1)
#define SPIEN (1u << 6)
#define TMR14 0x40002000u
#define TMR14_CTRL1 (TMR14 + 0x00)
#define TMR14_SWEVT (TMR14 + 0x14)
#define TMR14_CVAL (TMR14 + 0x24)
#define TMR14_DIV (TMR14 + 0x28)
#define WDT 0x40003000u
#define ADC 0x40012400u
#define ADC_STS (ADC + 0x00)
#define ADC_CTRL2 (ADC + 0x08)
#define ADC_OSQ3 (ADC + 0x34)
#define ADC_ODT (ADC + 0x4C)
#define APP_FLASH 0x08001000u
#ifndef CK_FL_LEN
#define CK_FL_LEN 0xF000                /* the 60 KB the bootloader gives the app */
#endif
#define BAUD 115200u
#define SPIN 200000u                    /* bound on every status wait (~150 ms at 8 MHz; a lock takes ~0.2 ms) */

/* the RF chip's setup registers (stock init, docs/uv21-characteristics.md); live ones left out */
static const uint8_t RF_REGS[] = {0x00, 0x10, 0x11, 0x12, 0x13, 0x14, 0x19, 0x1C, 0x1D, 0x1E,
                                  0x1F, 0x28, 0x29, 0x2A, 0x2C, 0x2F, 0x36, 0x37, 0x38, 0x39,
                                  0x3E, 0x43, 0x46, 0x48, 0x49, 0x4F, 0x53, 0x73, 0x77, 0x7B, 0x7D};
#define NRF (sizeof RF_REGS)
/* clock harmonics (60 MHz: 420, 480; 120 MHz: 360, 480) and the bench's own channels */
__attribute__((unused)) static const uint32_t RSSI_HZ[] = {420000000u, 440850000u, 446000000u, 480000000u,
                                   360000000u, 162550000u, 146520000u};
#define NRSSI (sizeof RSSI_HZ / sizeof RSSI_HZ[0])

typedef struct clk {
  uint32_t cfg, pll, misc2, baudr, spi, tdiv;   /* as the port left them */
  uint32_t home, mhz;                            /* the image's clock, the clock now (MHz) */
} clk_t;

typedef struct ctx {
  const um_api_t *a;
  clk_t c;
  uint16_t rf[NRF];
  uint32_t rf_ok;                                /* bit i: register i read the same twice at 60 */
  uint32_t fl_crc;
  int fails;
  char line[64];
  uint8_t *ram;
  uint32_t ram_len;
} ctx_t;

#define NI __attribute__((noinline))
/* uv21_mod.h's text helpers, called rather than inlined at every line (the module has many) */
static NI char *put(char *p, const char *s) { return um_put(p, s); }
static NI char *hex(char *p, uint32_t v, int digits) { return um_hex(p, v, digits); }
static NI char *dec(char *p, int32_t v) { return um_int(p, v); }

static NI void say(ctx_t *x) { x->a->say(x->line); }

static void feed(void) {
#if CK_DEADMAN
  REG(WDT) = 0xAAAA;
#endif
}

static NI uint32_t crc32(uint32_t crc, const uint8_t *p, uint32_t n) {
  crc = ~crc;
  while (n--) {
    crc ^= *p++;
    for (int k = 0; k < 8; k++) crc = crc & 1 ? crc >> 1 ^ 0xEDB88320u : crc >> 1;
  }
  return ~crc;
}

/* waits in TMR14's 100 us ticks, which the module keeps right at either clock */
__attribute__((unused)) static NI void wait_ms(ctx_t *x, uint32_t ms) {
  uint32_t t0 = x->a->ticks();
  while (x->a->ticks() - t0 < ms * 10u) feed();
}

static NI int spin_until(uint32_t addr, uint32_t mask, uint32_t want) {
  for (uint32_t n = 0; n < SPIN; n++)
    if ((REG(addr) & mask) == want) return 1;
  return 0;
}

/* PLLMULT as the AT32F421 codes it (its BSP's CRM_PLL_MULT_n): 0..13 = x2..x15, 14 and 15 = x16,
   16..63 = x17..x64. So the 60 MHz port's x15 is 13 (0b00_1101) and x30 is 29 (0b01_1101). (The
   first cut of this module took every code as n - 1 and read the radio's x15 as x14: the radio
   refused it, job uv21-clk120-1128a.) */
static uint32_t mult_of(uint32_t cfg) {
  uint32_t code = (cfg >> 29 & 3u) << 4 | (cfg >> 18 & 0xFu);
  return code < 15u ? code + 2u : code == 15u ? 16u : code + 1u;
}

static uint32_t mult_bits(uint32_t m) {
  uint32_t code = m <= 16u ? m - 2u : m - 1u;
  return (code & 0xFu) << 18 | (code >> 4 & 3u) << 29;
}

/* ADCDIV for ADCCLK <= 28 MHz: /2 (000), /4 (001), /6 (010), /8 (011) */
static uint32_t adc_bits(uint32_t mhz) {
  uint32_t d = mhz <= 56 ? 0u : mhz <= 112 ? 1u : 2u;
  return (d & 3u) << 14 | (d >> 2 & 1u) << 28;
}

static uint32_t baud_div(uint32_t mhz) { return (mhz * 1000000u + BAUD / 2) / BAUD; }

/* SPI2 MDIV (/2 << md) for 15 MHz or under: the ST7735S's top write speed */
static uint32_t spi_md(uint32_t mhz) {
  uint32_t md = 0;
  while (mhz > (15u << (md + 1)) && md < 7) md++;
  return md;
}

/* the peripherals the port derives from its clock, for mhz: as the port left them at home, else
   worked out for mhz. TMR14's count is kept. */
static void scale(ctx_t *x, uint32_t mhz) {
  uint32_t hz = mhz * 1000000u, cnt, home = mhz == x->c.home, md = spi_md(mhz);
  REG(USART1_BAUDR) = home ? x->c.baudr : baud_div(mhz);
  REG(SPI2_CTRL1) = x->c.spi & ~SPIEN;           /* MDIV changes only with SPI2 off */
  REG(SPI2_CTRL1) = home ? x->c.spi : (x->c.spi & ~(7u << 3)) | md << 3;
  cnt = REG(TMR14_CVAL);
  REG(TMR14_CTRL1) &= ~1u;
  REG(TMR14_DIV) = home ? x->c.tdiv : hz / 10000u - 1u;   /* 100 us */
  REG(TMR14_SWEVT) = 1u;                         /* the new DIV now (this zeroes the count) */
  REG(TMR14_CVAL) = cnt;
  REG(TMR14_CTRL1) |= 1u;
}

/* SCLK to the PLL at 4 MHz x mult; 1 when it runs there. On a lock that doesn't come it stays on
   HICK (8 MHz) and returns 0, and the caller goes back. Interrupts off throughout. */
static int switch_to(uint32_t cfg_keep, uint32_t mult, uint32_t adc) {
  uint32_t ok = 1;
  REG(CRM_CFG) &= ~3u;                           /* SCLK: HICK */
  ok &= spin_until(CRM_CFG, 0xCu, 0u);
  REG(CRM_CTRL) &= ~PLLEN;
  spin_until(CRM_CTRL, PLLSTBL, 0u);
  REG(CRM_CFG) = (cfg_keep & ~(3u | CFG_MULT_MASK | CFG_ADC_MASK)) | mult_bits(mult) | adc;
  REG(CRM_CTRL) |= PLLEN;
  if (!ok || !spin_until(CRM_CTRL, PLLSTBL, PLLSTBL)) return 0;
  REG(CRM_MISC2) |= 3u << 4;                     /* auto step on for the jump */
  REG(CRM_CFG) |= 2u;                            /* SCLK: PLL */
  ok = spin_until(CRM_CFG, 0xCu, 0x8u);
  REG(CRM_MISC2) &= ~(3u << 4);
  return (int)ok;
}

static NI void spi_out(uint32_t b) {
  while (!(REG(SPI2_STS) & SPI_TDBE)) {}
  REG(SPI2_DT) = b;
}

static void spi_idle(void) {
  while (!(REG(SPI2_STS) & SPI_TDBE)) {}
  while (REG(SPI2_STS) & SPI_BF) {}
}

static uint32_t irq_off(void) {
  uint32_t pm;
  __asm__ volatile("mrs %0, primask\n cpsid i" : "=r"(pm)::"memory");
  return pm;
}

static void irq_on(uint32_t pm) {
  if (!(pm & 1)) __asm__ volatile("cpsie i" ::: "memory");
}

/* to 4 MHz x mult (the image's own: put back as it was); the clock it then runs at */
static uint32_t go(ctx_t *x, uint32_t mult) {
  uint32_t back = mult == mult_of(x->c.cfg), mhz = 4u * mult;
  spin_until(USART1_STS, TDC, TDC);              /* the last line out at the old speed */
  spi_idle();
  uint32_t pm = irq_off();
  if (switch_to(x->c.cfg, mult, back ? x->c.cfg & CFG_ADC_MASK : adc_bits(mhz))) {
    if (back) REG(CRM_MISC2) = x->c.misc2;
    x->c.mhz = mhz;
  } else if (!back && switch_to(x->c.cfg, mult_of(x->c.cfg), x->c.cfg & CFG_ADC_MASK)) {
    x->fails |= F_LOCK;
    x->c.mhz = 4u * mult_of(x->c.cfg);
  } else {
    x->fails |= back ? F_BACK : F_LOCK | F_BACK;
    x->c.mhz = 8;                                /* on HICK: the deadman will reset us */
  }
  scale(x, x->c.mhz);
  irq_on(pm);
  feed();
  return x->c.mhz;
}

static NI char *kv(char *p, const char *k, uint32_t v, int digits) {
  p = put(p, k);
  return hex(p, v, digits);
}

static void state(ctx_t *x, const char *tag) {
  char *p = put(x->line, "clk ");
  p = put(p, tag);
  p = put(p, " mhz=");
  p = dec(p, (int32_t)x->c.mhz);
  p = kv(p, " c=", REG(CRM_CFG), 8);           /* CRM_CFG */
  p = kv(p, " p=", REG(FLASH_PSR), 3);         /* FLASH_PSR */
  p = kv(p, " b=", REG(USART1_BAUDR), 4);      /* USART1 BAUDR */
  p = kv(p, " s=", REG(SPI2_CTRL1), 3);        /* SPI2 CTRL1 */
  kv(p, " t=", REG(TMR14_DIV), 4);             /* TMR14 DIV */
  say(x);
}

static NI void rf_read(ctx_t *x, uint16_t *v) {
  for (unsigned i = 0; i < NRF; i++) v[i] = x->a->fd_read(RF_REGS[i]);
}

/* the registers against the 60 MHz reads; differences named, F_RF set */
static int rf_check(ctx_t *x, const char *tag, int quiet) {
  uint16_t v[NRF];
  int bad = 0;
  rf_read(x, v);
  for (unsigned i = 0; i < NRF; i++) {
    if (!(x->rf_ok >> i & 1) || v[i] == x->rf[i]) continue;
    bad++;
    if (quiet || bad > 4) continue;
    char *p = put(x->line, "clk rf bad ");
    p = put(p, tag);
    p = kv(p, " r=", RF_REGS[i], 2);
    p = kv(p, " was=", x->rf[i], 4);
    kv(p, " now=", v[i], 4);
    say(x);
  }
  if (!quiet) {
    char *p = put(x->line, "clk rf ");
    p = put(p, tag);
    p = kv(p, " id=", v[0], 4);
    p = put(p, " n=");
    p = dec(p, (int32_t)__builtin_popcount(x->rf_ok));
    p = put(p, " bad=");
    dec(p, bad);
    say(x);
  }
  if (bad) x->fails |= F_RF;
  return bad;
}

static NI uint32_t flash_crc(void) { return crc32(0, (const uint8_t *)APP_FLASH, CK_FL_LEN); }

static int fl_check(ctx_t *x, const char *tag, int quiet) {
  uint32_t c = flash_crc();
  int bad = c != x->fl_crc;
  if (!quiet) {
    char *p = put(x->line, "clk fl ");
    p = put(p, tag);
    p = kv(p, " crc=", c, 8);
    p = put(p, bad ? " BAD" : " ok");
    say(x);
  }
  if (bad) x->fails |= F_FLASH;
  return bad;
}

static void cpu(ctx_t *x, const char *tag) {
  uint32_t t0 = x->a->ticks(), c = 0;
  for (int i = 0; i < 8; i++) c = crc32(c, x->ram, 2048);
  uint32_t t1 = x->a->ticks();
  for (int i = 0; i < 4000; i++) c += x->a->ms();
  uint32_t t2 = x->a->ticks();
  char *p = put(x->line, "clk cpu ");
  p = put(p, tag);
  p = put(p, " ram=");
  p = dec(p, (int32_t)(t1 - t0));
  p = put(p, " call=");
  p = dec(p, (int32_t)(t2 - t1));
  p = put(p, " (100us)");
  (void)c;
  say(x);
}

static uint32_t adc_read(uint32_t ch) {
  uint32_t sum = 0;
  REG(ADC_OSQ3) = (REG(ADC_OSQ3) & ~0x1Fu) | ch;
  for (int i = 0; i < 16; i++) {
    REG(ADC_CTRL2) |= 1u << 22;                  /* OCSWTRG */
    if (!spin_until(ADC_STS, 1u << 1, 1u << 1)) return 0xFFFF;
    sum += REG(ADC_ODT) & 0xFFFFu;
  }
  return sum / 16u;
}

static void adc(ctx_t *x, const char *tag) {
  char *p = put(x->line, "clk adc ");
  p = put(p, tag);
  p = put(p, " batt=");
  dec(p, (int32_t)adc_read(1));
  say(x);
}

static void rssi(ctx_t *x, const char *tag) {
#if CK_RSSI
  char *p = put(x->line, "clk rssi ");
  p = put(p, tag);
  for (unsigned i = 0; i < NRSSI; i++) {
    x->a->tune(RSSI_HZ[i], 1);
    wait_ms(x, 60);
    int s = 0;
    for (int k = 0; k < 8; k++) s += um_rssi2(x->a), wait_ms(x, 2);
    *p++ = ' ';
    p = dec(p, s / 16);                       /* dBm, rounded down */
  }
  x->a->tune(x->a->rx_hz(), 1);
  say(x);
#else
  (void)x, (void)tag;
#endif
}

/* the test card: colour bars, a one-pixel checkerboard, a ramp, every data bit alone */
static uint32_t card(unsigned x, unsigned y) {
  static const uint16_t bars[8] = {0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000};
  if (y < 40) return bars[x >> 4];
  if (y < 80) return (x ^ y) & 1 ? 0xFFFF : 0x0000;
  if (y < 120) return (x >> 2) << 11 | ((y - 80) & 63u) << 5 | ((x + y) & 31u);
  return 1u << ((x + y) & 15u);
}

/* a command and its arguments through SPI2; CS stays low after RAMWR for the pixels */
static void lcd_cmd(uint32_t c, const uint8_t *arg, int n) {
  spi_idle();
  lo(GPIOC, DC);
  lo(GPIOB, CS);
  spi_out(c);
  spi_idle();
  hi(GPIOC, DC);
  for (int i = 0; i < n; i++) spi_out(arg[i]);
  spi_idle();
  if (c != 0x2C) hi(GPIOB, CS);
}

/* draws the card through SPI2 at the clock it now has, then reads it back; differing pixels */
static int pat(ctx_t *x, const char *tag, int flag) {
  const uint8_t ca[4] = {0, 0, 0, SD_W - 1}, ra[4] = {0, 0, 0, 159};
  uint32_t t0 = x->a->ticks();
  lcd_cmd(0x2A, ca, 4);
  lcd_cmd(0x2B, ra, 4);
  lcd_cmd(0x2C, 0, 0);                           /* RAMWR, CS stays low for the pixels */
  for (unsigned y = 0; y < 160; y++) {
    for (unsigned c = 0; c < SD_W; c++) {
      uint32_t v = card(c, y);
      spi_out(v >> 8);
      spi_out(v & 0xFFu);
    }
    feed();
  }
  spi_idle();
  hi(GPIOB, CS);
  uint32_t t1 = x->a->ticks();
  sd_pins_t pins;
  uint8_t *raw = x->ram, *rgb = x->ram + ((SD_BYTES + 3) & ~3u);
  int bad = 0, first = -1;
  sd_take(&pins);
  for (unsigned y = SD_Y0; y < SD_Y0 + SD_ROWS && y < 160; y++) {
    sd_read_row((int)y, raw, rgb);
    for (unsigned c = 0; c < SD_W; c++)
      if ((uint32_t)(rgb[2 * c] << 8 | rgb[2 * c + 1]) != card(c, y)) {
        if (first < 0) first = (int)(y << 8 | c);
        bad++;
      }
  }
  sd_give(&pins);
  char *p = put(x->line, "clk pat ");
  p = put(p, tag);
  p = put(p, " draw=");
  p = dec(p, (int32_t)(t1 - t0));
  p = put(p, " bad=");
  p = dec(p, bad);
  if (bad) p = kv(p, " first=", (uint32_t)first, 4);
  say(x);
  if (bad) x->fails |= flag;
  return bad;
}

static void grab(ctx_t *x, const char *tag) {
#if CK_GRAB
  char *p = put(x->line, "clk grab ");
  put(p, tag);
  say(x);
  sd_grab(x->a, x->ram, x->ram_len);
  feed();
#else
  (void)x, (void)tag;
#endif
}

static void deadman(ctx_t *x) {
#if CK_DEADMAN
  REG(WDT) = 0x5555;                             /* unlock DIV and RLD */
  REG(WDT + 0x04) = 6;                           /* LICK / 256 */
  REG(WDT + 0x08) = 0xFFF;                       /* 4096 counts: ~26 s at 40 kHz */
  spin_until(WDT + 0x0C, 3u, 0u);
  REG(WDT) = 0xAAAA;
  REG(WDT) = 0xCCCC;                             /* on until the next reset */
  put(x->line, "clk deadman on: the radio resets ~26 s after this module ends");
  say(x);
#else
  (void)x;
#endif
}

int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) {
  ctx_t x;
  uint16_t again[NRF];
  x.a = api;
  x.ram = ram;
  x.ram_len = ram_len;
  x.fails = 0;
  x.c.cfg = REG(CRM_CFG);
  x.c.pll = REG(CRM_PLL);
  x.c.misc2 = REG(CRM_MISC2);
  x.c.baudr = REG(USART1_BAUDR);
  x.c.spi = REG(SPI2_CTRL1);
  x.c.tdiv = REG(TMR14_DIV);
  x.c.home = x.c.mhz = 4u * mult_of(x.c.cfg);
  state(&x, "home");
  /* an image this module knows: on the PLL from HICK/2 x 15 or x 30, buses undivided, 3 wait
     states, the PLL in its normal mode, and USART1, SPI2 and TMR14 set for that clock */
  uint32_t m = mult_of(x.c.cfg);
  if ((x.c.cfg & 0xFu) != 0xAu || (x.c.cfg & (3u << 16 | 0x3FF0u)) || (m != 15 && m != 30) ||
      m == CK_MULT || (REG(FLASH_PSR) & 7u) < 3u || (x.c.pll & 1u << 31) ||
      x.c.baudr != baud_div(x.c.home) || (x.c.spi >> 3 & 7u) != spi_md(x.c.home) ||
      x.c.tdiv != x.c.home * 100u - 1u) {
    put(x.line, "clk refused: not a clock setup this module knows");
    say(&x);
    return F_REFUSED;
  }

  /* home: what the checks compare with */
  rf_read(&x, x.rf);
  rf_read(&x, again);
  x.rf_ok = 0;
  for (unsigned i = 0; i < NRF; i++)
    if (again[i] == x.rf[i]) x.rf_ok |= 1u << i;
  x.fl_crc = flash_crc();
  rf_check(&x, "home", 0);
  fl_check(&x, "home", 0);
  cpu(&x, "home");
  adc(&x, "home");
  rssi(&x, "home");
  grab(&x, "surf home");

  deadman(&x);
  char *p = put(x.line, "clk go new mhz=");
  dec(p, 4 * CK_MULT);
  say(&x);
  go(&x, CK_MULT);
  state(&x, "new");
  if (x.c.mhz == 4u * CK_MULT) {
    if ((REG(USART1_BAUDR) & 0xFFFFu) != baud_div(x.c.mhz) || mult_of(REG(CRM_CFG)) != CK_MULT ||
        (REG(CRM_CFG) & 0xCu) != 0x8u || (REG(SPI2_CTRL1) >> 3 & 7u) != spi_md(x.c.mhz))
      x.fails |= F_STATE;
    rf_check(&x, "new", 0);
    fl_check(&x, "new", 0);
    cpu(&x, "new");
    adc(&x, "new");
    rssi(&x, "new");
    grab(&x, "surf new");
    pat(&x, "new", F_PAT_NEW);
    grab(&x, "card new");
    uint32_t t0 = api->ticks(), n = 0, bad = 0;
    while (api->ticks() - t0 < CK_SOAK_S * 10000u) {
      bad += (uint32_t)rf_check(&x, "soak", 1) + (uint32_t)fl_check(&x, "soak", 1);
      n++;
      feed();
    }
    p = put(x.line, "clk soak s=");
    p = dec(p, CK_SOAK_S);
    p = put(p, " rounds=");
    p = dec(p, (int32_t)n);
    p = put(p, " bad=");
    dec(p, (int32_t)bad);
    say(&x);
    if (bad) x.fails |= F_SOAK;
  }

  p = put(x.line, "clk go home mhz=");
  dec(p, (int32_t)x.c.home);
  say(&x);
  go(&x, mult_of(x.c.cfg));
  state(&x, "back");
  if (REG(CRM_CFG) != x.c.cfg || (REG(USART1_BAUDR) & 0xFFFFu) != x.c.baudr ||
      REG(SPI2_CTRL1) != x.c.spi || REG(TMR14_DIV) != x.c.tdiv || REG(CRM_MISC2) != x.c.misc2)
    x.fails |= F_STATE;
  rf_check(&x, "back", 0);
  fl_check(&x, "back", 0);
  pat(&x, "back", F_PAT_BACK);
  p = put(x.line, "clk done fails=");
  kv(p, "", (uint32_t)x.fails, 3);
  say(&x);
  return x.fails;
}
