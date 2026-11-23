/* UV-21 Pro bring-up image: the first blowfang code that runs as the radio's app.
 *
 * What it is for: to learn this board from the radio itself before anything bigger goes on it.
 * It reports over the K-plug serial line (115200 8N1, text lines) and, once the LCD pins are known,
 * on the screen:
 *   - how the bootloader left the clocks, the chip ID, flash size, supply voltage (internal
 *     reference), whether the external crystal starts;
 *   - the RF chip's ID and its 128 registers (reads only), the LCD controller's ID, the SPI flash's
 *     JEDEC ID;
 *   - every key press: the key matrix, keys on their own pins, and a census of every input pin, so
 *     a key on a pin nobody has mapped still shows up;
 *   - PTT, which shares the serial receive pin: a press reads as a break.
 *
 * What it never does: transmit (the RF bus code has no register write at all: every transaction's
 * address byte has the read bit set), write or erase MCU flash (no FLASH register is touched), or
 * send the SPI flash anything but release-from-power-down (AB) and JEDEC ID (9F). Pins not
 * confirmed for this board (board.h) are never driven. SWD (PA13/PA14) stays SWD. The speaker amplifier is left as the bootloader left it.
 *
 * Console, one byte each: ? help, i report, r RF registers, l redraw the screen, p pin census
 * on/off, O power off (drop the latch), R reset (and b backlight, f flashlight on a board that
 * names those pins). The bootloader's
 * command 42 frame (AA 42 ...) also resets, so `tools/uv21_kdh.py probe --wait-boot-s` reaches flash
 * mode from this image with no hands: it keeps sending that frame and the bootloader catches the
 * next one.
 *
 * SPDX-License-Identifier: MIT */
#include <stdint.h>

#include "board.h"

#define BANNER "blowfang uv21 bringup 2"
#ifndef BUILD_ID
#define BUILD_ID "local"
#endif

#define REG(a) (*(volatile uint32_t *)(a))
#define CRM_CTRL REG(0x40021000)
#define CRM_CFG REG(0x40021004)
#define CRM_AHBEN REG(0x40021014)
#define CRM_APB2EN REG(0x40021018)
#define GPIO_BASE(port) (0x48000000u + (uint32_t)(port) * 0x400u)
#define G_CFGR 0x00
#define G_PULL 0x0C
#define G_IDT 0x10
#define G_SCR 0x18
#define G_MUXL 0x20
#define G_MUXH 0x24
#define G_CLR 0x28
#define U_STS REG(0x40013800)
#define U_DT REG(0x40013804)
#define U_BAUDR REG(0x40013808)
#define U_CTRL1 REG(0x4001380C)
#define ADC_STS REG(0x40012400)
#define ADC_CTRL2 REG(0x40012408)
#define ADC_SPT1 REG(0x4001240C)
#define ADC_SPT2 REG(0x40012410)
#define ADC_OSQ3 REG(0x40012434)
#define ADC_ODT REG(0x4001244C)
#define WDT_CMD REG(0x40003000)
#define SYST_CSR REG(0xE000E010)
#define SYST_RVR REG(0xE000E014)
#define SYST_CVR REG(0xE000E018)
#define SCB_VTOR REG(0xE000ED08)
#define SCB_AIRCR REG(0xE000ED0C)
#define DBG_IDCODE REG(0xE0042000)
#define FLASH_SIZE_KB (*(volatile uint16_t *)0x1FFFF7E0)

#define HZ 8000000u                       /* the internal 8 MHz clock, no PLL */
#define COUNT(a) (sizeof(a) / sizeof((a)[0]))

/* ---- pins ---- */

static inline uint32_t gbase(uint32_t pin) { return GPIO_BASE(pin >> 4); }

static inline void gset(uint32_t pin, uint32_t high) {
    REG(gbase(pin) + (high ? G_SCR : G_CLR)) = 1u << (pin & 15);
}

__attribute__((unused)) static inline uint32_t gget(uint32_t pin) { return (REG(gbase(pin) + G_IDT) >> (pin & 15)) & 1u; }

static void gfield(uint32_t pin, uint32_t off, uint32_t v) {   /* the pin's 2-bit field */
    uint32_t s = (pin & 15) * 2;
    REG(gbase(pin) + off) = (REG(gbase(pin) + off) & ~(3u << s)) | (v << s);
}

enum { IN = 0, OUT = 1, AF = 2, ANALOG = 3 };
enum { FLOAT = 0, UP = 1, DOWN = 2 };

static void gmode(uint32_t pin, uint32_t mode) { gfield(pin, G_CFGR, mode); }
static void gpull(uint32_t pin, uint32_t pull) { gfield(pin, G_PULL, pull); }

static void gout(uint32_t pin, uint32_t high) {               /* level first, then drive */
    gset(pin, high);
    gmode(pin, OUT);
}

static void gaf(uint32_t pin, uint32_t n) {
    uint32_t b = pin & 15, off = b < 8 ? G_MUXL : G_MUXH, s = (b & 7) * 4;
    REG(gbase(pin) + off) = (REG(gbase(pin) + off) & ~(15u << s)) | (n << s);
    gmode(pin, AF);
}

/* ---- time: SysTick free-running, extended in software (poll at least every 2 s) ---- */

static uint32_t last_cvr, cycles;

static void kick(void) { WDT_CMD = 0xAAAA; }   /* reload, in case the bootloader started it */

static uint32_t now(void) {
    uint32_t v = SYST_CVR;
    cycles += (last_cvr - v) & 0xFFFFFFu;
    last_cvr = v;
    return cycles;
}

static void delay_us(uint32_t us) {
    uint32_t t = now();
    while (now() - t < us * (HZ / 1000000u)) kick();
}

__attribute__((unused)) static void delay_ms(uint32_t ms) {
    while (ms--) delay_us(1000);
}

/* ---- serial ---- */

static uint32_t breaks, rx_errors;

static void putch(char c) {
    while (!(U_STS & (1u << 7))) {}       /* TDBE */
    U_DT = (uint8_t)c;
}

static void puts_(const char *s) {
    while (*s) putch(*s++);
}

static void nl(void) { puts_("\r\n"); }

static void hex(uint32_t v, int digits) {
    while (digits--) putch("0123456789abcdef"[(v >> (digits * 4)) & 15]);
}

static void dec(uint32_t v) {
    char b[11];
    int n = 0;
    do b[n++] = (char)('0' + v % 10); while (v /= 10);
    while (n) putch(b[--n]);
}

static void kv_hex(const char *k, uint32_t v, int digits) {
    putch(' ');
    puts_(k);
    putch('=');
    hex(v, digits);
}

static void kv_dec(const char *k, uint32_t v) {
    putch(' ');
    puts_(k);
    putch('=');
    dec(v);
}

static int getch(void) {                  /* a byte, -1 for none, -2 for a break (PTT on the 5RM) */
    uint32_t s = U_STS;
    if (s & 0xF) {                        /* parity, framing, noise or overrun: STS then DT clears */
        uint8_t d = (uint8_t)U_DT;
        if ((s & 2u) && d == 0) {
            breaks++;
            return -2;
        }
        rx_errors++;
        return -1;
    }
    if (s & (1u << 5)) return (uint8_t)U_DT;
    return -1;
}

static void tx_drain(void) {
    while (!(U_STS & (1u << 6))) {}       /* TDC */
}

/* ---- RF chip: reads only ---- */

#ifdef RF_SCN
static void rf_byte(uint32_t b) {
    for (int i = 7; i >= 0; i--) {
        gset(RF_SCK, 0);
        gset(RF_SDA, (b >> i) & 1);
        delay_us(1);
        gset(RF_SCK, 1);
        delay_us(1);
    }
}

static uint32_t rf_read(uint32_t reg) {
    uint32_t v = 0;
    gset(RF_SCN, 0);
    delay_us(1);
    rf_byte(0x80u | reg);                 /* bit 7: read. There is no write path in this image */
    gset(RF_SCK, 0);
    gmode(RF_SDA, IN);
    delay_us(1);
    for (int i = 0; i < 16; i++) {
        gset(RF_SCK, 1);
        v = v << 1 | gget(RF_SDA);
        delay_us(1);
        gset(RF_SCK, 0);
        delay_us(1);
    }
    gmode(RF_SDA, OUT);
    gset(RF_SCN, 1);
    delay_us(1);
    gset(RF_SDA, 0);
    return v;
}

static void rf_init(void) {
    gout(RF_SCN, 1);
    gout(RF_SCK, 0);
    gout(RF_SDA, 0);
}

static void rf_regs(void) {
    for (uint32_t r = 0; r < 0x80; r += 16) {
        puts_("rf regs ");
        hex(r, 2);
        putch(':');
        for (uint32_t i = 0; i < 16; i++) {
            putch(' ');
            hex(rf_read(r + i), 4);
        }
        nl();
    }
}
#endif

/* ---- SPI flash: JEDEC ID only ---- */

#ifdef NOR_CS
static uint32_t nor_byte(uint32_t b) {
    uint32_t in = 0;
    for (int i = 7; i >= 0; i--) {
        gset(NOR_SCK, 0);
        gset(NOR_MOSI, (b >> i) & 1);
        delay_us(1);
        gset(NOR_SCK, 1);
        in = in << 1 | gget(NOR_MISO);
        delay_us(1);
    }
    gset(NOR_SCK, 0);
    return in;
}

static uint32_t nor_jedec(void) {
    gout(NOR_CS, 1);
    gout(NOR_SCK, 0);
    gout(NOR_MOSI, 0);
    gmode(NOR_MISO, IN);
    gpull(NOR_MISO, UP);
    gset(NOR_CS, 0);
    nor_byte(0xAB);                       /* release from deep power-down (stock sends it at init) */
    gset(NOR_CS, 1);
    delay_us(50);
    gset(NOR_CS, 0);
    nor_byte(0x9F);                       /* JEDEC ID */
    uint32_t id = nor_byte(0) << 16;
    id |= nor_byte(0) << 8;
    id |= nor_byte(0);
    gset(NOR_CS, 1);
    return id;
}
#endif

/* ---- LCD: bit-banged 4-wire SPI ---- */

#ifdef LCD_CS
static const uint16_t font3x5[64] = {     /* ' ' .. '_': rows of 3 bits, top row high */
    0x0000, 0x0000, 0x0000, 0x5f7d, 0x0000, 0x0000, 0x0000, 0x0000,
    0x1491, 0x4494, 0x55d5, 0x05d0, 0x0014, 0x01c0, 0x0002, 0x12a4,
    0x7b6f, 0x2c97, 0x73e7, 0x72cf, 0x5bc9, 0x79cf, 0x79ef, 0x7249,
    0x7bef, 0x7bcf, 0x0410, 0x0000, 0x1511, 0x0e38, 0x4454, 0x72c2,
    0x0000, 0x2bed, 0x6bae, 0x3923, 0x6b6e, 0x79a7, 0x79a4, 0x396b,
    0x5bed, 0x7497, 0x126a, 0x5bad, 0x4927, 0x5fed, 0x6b6d, 0x2b6a,
    0x6ba4, 0x2b73, 0x6bad, 0x388e, 0x7492, 0x5b6f, 0x5b6a, 0x5bfd,
    0x5aad, 0x5a92, 0x72a7, 0x0000, 0x0000, 0x0000, 0x0000, 0x0007,
};

#define BLACK 0x0000
#define WHITE 0xFFFF
#define RED 0xF800
#define GREEN 0x07E0
#define BLUE 0x001F
#define AMBER 0xFD20
#define GREY 0x8410

static uint32_t lcd_rddid, lcd_rddst;

static void lcd_bits(uint32_t b) {
    for (int i = 7; i >= 0; i--) {
        gset(LCD_SCK, 0);
        gset(LCD_SDA, (b >> i) & 1);
        gset(LCD_SCK, 1);
    }
}

static void lcd_cmd(uint32_t c) {
    gset(LCD_DC, 0);
    gset(LCD_CS, 0);
    lcd_bits(c);
    gset(LCD_CS, 1);
    gset(LCD_DC, 1);
}

static void lcd_cmd_args(uint32_t c, const uint8_t *a, uint32_t n) {
    lcd_cmd(c);
    gset(LCD_CS, 0);
    while (n--) lcd_bits(*a++);
    gset(LCD_CS, 1);
}

/* a read command: the controller answers on SDA after the command byte. 32 clocks, raw: on an
   ST7735 RDDID gives a dummy bit, then the 24-bit ID */
static uint32_t lcd_read(uint32_t c) {
    uint32_t v = 0;
    gset(LCD_DC, 0);
    gset(LCD_CS, 0);
    lcd_bits(c);
    gset(LCD_DC, 1);
    gset(LCD_SCK, 0);
    gmode(LCD_SDA, IN);
    gpull(LCD_SDA, UP);                   /* nobody answering reads as all ones */
    for (int i = 0; i < 32; i++) {
        gset(LCD_SCK, 1);
        v = v << 1 | gget(LCD_SDA);
        gset(LCD_SCK, 0);
    }
    gset(LCD_CS, 1);
    gpull(LCD_SDA, FLOAT);
    gmode(LCD_SDA, OUT);
    return v;
}

static void lcd_window(uint32_t x, uint32_t y, uint32_t w, uint32_t h) {
    uint8_t c[4] = {0, (uint8_t)x, 0, (uint8_t)(x + w - 1)};
    uint8_t r[4] = {0, (uint8_t)y, 0, (uint8_t)(y + h - 1)};
    lcd_cmd_args(0x2A, c, 4);             /* CASET */
    lcd_cmd_args(0x2B, r, 4);             /* RASET */
    lcd_cmd(0x2C);                        /* RAMWR: pixels follow */
    gset(LCD_CS, 0);
}

static void lcd_fill(uint32_t x, uint32_t y, uint32_t w, uint32_t h, uint32_t colour) {
    lcd_window(x, y, w, h);
    for (uint32_t n = w * h; n; n--) {
        lcd_bits(colour >> 8);
        lcd_bits(colour);
        if (!(n & 1023)) kick();
    }
    gset(LCD_CS, 1);
}

/* one character cell, 8 x 12: the 3x5 glyph doubled, one blank column and row around it */
static void lcd_char(uint32_t x, uint32_t y, char ch, uint32_t fg, uint32_t bg) {
    if (ch >= 'a' && ch <= 'z') ch = (char)(ch - 32);
    uint32_t g = (ch >= 0x20 && ch < 0x60) ? font3x5[ch - 0x20] : 0x72c2;   /* unknown: ? */
    lcd_window(x, y, 8, 12);
    for (uint32_t cy = 0; cy < 12; cy++)
        for (uint32_t cx = 0; cx < 8; cx++) {
            uint32_t gx = (cx - 1) / 2, gy = (cy - 1) / 2;
            uint32_t on = cx >= 1 && cx < 7 && cy >= 1 && cy < 11 &&
                          ((g >> ((4 - gy) * 3 + (2 - gx))) & 1);
            uint32_t c = on ? fg : bg;
            lcd_bits(c >> 8);
            lcd_bits(c);
        }
    gset(LCD_CS, 1);
}

static void lcd_text(uint32_t x, uint32_t y, const char *s, uint32_t fg) {
    for (; *s && x + 8 <= LCD_W; s++, x += 8) lcd_char(x, y, *s, fg, BLACK);
}

static void lcd_hexat(uint32_t x, uint32_t y, uint32_t v, int digits, uint32_t fg) {
    char b[9];
    for (int i = 0; i < digits; i++) b[i] = "0123456789ABCDEF"[(v >> ((digits - 1 - i) * 4)) & 15];
    b[digits] = 0;
    lcd_text(x, y, b, fg);
}

static void lcd_decat(uint32_t x, uint32_t y, uint32_t v, uint32_t fg) {
    char b[11];
    int n = 10;
    b[n] = 0;
    do b[--n] = (char)('0' + v % 10); while (v /= 10);
    while (n > 4) b[--n] = ' ';           /* right-aligned in 6 cells, so old digits get covered */
    lcd_text(x, y, b + n, fg);
}

static void lcd_init(void) {
    gout(LCD_CS, 1);
    gout(LCD_DC, 1);
    gout(LCD_SCK, 0);
    gout(LCD_SDA, 0);
    gout(LCD_RST, 1);
    delay_ms(10);
    gset(LCD_RST, 0);
    delay_ms(10);
    gset(LCD_RST, 1);
    delay_ms(120);
    lcd_rddid = lcd_read(0x04);           /* RDDID (stock never reads: SDA may not answer) */
    lcd_rddst = lcd_read(0x09);           /* RDDST */
    lcd_cmd(0x11);                        /* SLPOUT */
    delay_ms(120);
    /* the controller set-up the stock firmware sends (ST7735S frame rate, inversion, power and
       VCOM registers), less its gamma tables, which stay at the controller's defaults */
    static const uint8_t init[] = {
        0xB1, 3, 0x05, 0x3A, 0x3A,
        0xB2, 3, 0x05, 0x3A, 0x3A,
        0xB3, 6, 0x05, 0x3A, 0x3A, 0x05, 0x3A, 0x3A,
        0xB4, 1, 0x03,
        0xC0, 3, 0x62, 0x02, 0x04,
        0xC1, 1, 0xC0,
        0xC2, 2, 0x0D, 0x00,
        0xC3, 2, 0x8D, 0x6A,
        0xC4, 2, 0x8D, 0xEE,
        0xC5, 1, 0x12,
        0xFC, 1, 0x8C,
        0x36, 1, LCD_MADCTL,
        0x3A, 1, 0x05,                    /* 16 bits per pixel */
    };
    for (uint32_t i = 0; i < sizeof init; i += 2u + init[i + 1]) lcd_cmd_args(init[i], &init[i + 2], init[i + 1]);
    lcd_cmd(0x29);                        /* DISPON */
}
#endif

/* ---- keys and pins ---- */

#ifdef KEY_COLS
static const uint8_t key_cols[] = {KEY_COLS};
static const uint8_t key_rows[] = {KEY_ROWS};
#endif
#ifdef KEY_DIRECT
static const uint8_t key_direct[] = {KEY_DIRECT};
#endif

static void keys_init(void) {
#ifdef KEY_COLS
    for (uint32_t i = 0; i < COUNT(key_cols); i++) gout(key_cols[i], 1);
    for (uint32_t i = 0; i < COUNT(key_rows); i++) {
        gmode(key_rows[i], IN);
        gpull(key_rows[i], UP);
    }
#endif
#ifdef KEY_DIRECT
    for (uint32_t i = 0; i < COUNT(key_direct); i++) {
        gmode(key_direct[i], IN);
        gpull(key_direct[i], UP);
    }
#endif
}

/* pressed keys: matrix bit (col * rows + row); side bit r for a key that grounds row r itself
   (low with no column driven: the UV-21's side keys); direct key bit i (KEY_DIRECT order) */
struct keys {
    uint32_t matrix, side, direct;
};

#ifdef KEY_COLS
_Static_assert(sizeof key_cols * sizeof key_rows <= 32, "the matrix must fit 32 bits");
#endif

static struct keys keys_scan(void) {
    struct keys k = {0, 0, 0};
#ifdef KEY_COLS
    delay_us(5);
    for (uint32_t r = 0; r < COUNT(key_rows); r++)   /* no column driven: a row low is a side key */
        if (!gget(key_rows[r])) k.side |= 1u << r;
    for (uint32_t c = 0; c < COUNT(key_cols); c++) {
        gset(key_cols[c], 0);
        delay_us(5);
        for (uint32_t r = 0; r < COUNT(key_rows); r++)
            if (!(k.side >> r & 1) && !gget(key_rows[r])) k.matrix |= 1u << (c * COUNT(key_rows) + r);
        gset(key_cols[c], 1);
    }
#endif
#ifdef KEY_DIRECT
    for (uint32_t i = 0; i < COUNT(key_direct); i++)
        if (!gget(key_direct[i])) k.direct |= 1u << i;
#endif
    return k;
}

static int keys_same(struct keys a, struct keys b) {
    return a.matrix == b.matrix && a.side == b.side && a.direct == b.direct;
}

/* the name of the one key held, from board.h's tables (0: none, several, or not named yet) */
static const char *key_name(struct keys k) {
#if defined(KEY_COLS) && defined(KEY_NAMES)
    static const char *const names[] = {KEY_NAMES};
    static const char *const side_names[] = {SIDE_NAMES};
    uint32_t n = 0, m = k.matrix, s = k.side;
    const char *name = 0;
    for (uint32_t b = 0; b < 32; b++) {
        if (m >> b & 1) {
            n++;
            name = b < COUNT(names) ? names[b] : 0;
        }
        if (s >> b & 1) {
            n++;
            name = b < COUNT(side_names) ? side_names[b] : 0;
        }
    }
    return n == 1 && !k.direct ? name : 0;
#else
    (void)k;
    return 0;
#endif
}

static const uint8_t ports[] = {PORT_A, PORT_B, PORT_C, PORT_F};

static uint32_t input_mask(uint32_t port) {   /* pins in input mode (a census of what we don't drive) */
    uint32_t cfg = REG(GPIO_BASE(port) + G_CFGR), m = 0;
    for (uint32_t b = 0; b < 16; b++)
        if (!((cfg >> (b * 2)) & 3)) m |= 1u << b;
    return m;
}

static void census_read(uint32_t *v) {
    for (uint32_t i = 0; i < COUNT(ports); i++)
        v[i] = REG(GPIO_BASE(ports[i]) + G_IDT) & input_mask(ports[i]) & 0xFFFF;
}

static void census_print(const uint32_t *v) {
    puts_("pins");
    kv_hex("a", v[0], 4);
    kv_hex("b", v[1], 4);
    kv_hex("c", v[2], 4);
    kv_hex("f", v[3], 4);
    nl();
}

/* ---- ADC: the internal reference (supply voltage) and any board channels ---- */

#ifdef ADC_CHANNELS
static const uint8_t adc_channels[] = {ADC_CHANNELS};
#endif

static void adc_init(void) {
    CRM_APB2EN |= 1u << 9;
#ifdef ADC_CHANNELS
    for (uint32_t i = 0; i < COUNT(adc_channels); i++) {
        uint32_t c = adc_channels[i];
        if (c < 8) gmode(PIN(PORT_A, c), ANALOG);
        else if (c < 10) gmode(PIN(PORT_B, c - 8), ANALOG);
    }
#endif
    ADC_CTRL2 = 1u;                       /* ADCEN */
    delay_us(20);
    ADC_CTRL2 |= 1u << 3;                 /* ADCALINIT */
    for (uint32_t t = 0; (ADC_CTRL2 & (1u << 3)) && t < 100000; t++) {}
    ADC_CTRL2 |= 1u << 2;                 /* ADCAL */
    for (uint32_t t = 0; (ADC_CTRL2 & (1u << 2)) && t < 100000; t++) {}
    ADC_SPT1 = 0x00FFFFFFu;               /* slowest sampling everywhere */
    ADC_SPT2 = 0x3FFFFFFFu;
    ADC_CTRL2 |= (7u << 17) | (1u << 20) | (1u << 23);   /* software trigger, temp + reference on */
}

static uint32_t adc_read(uint32_t ch) {
    ADC_OSQ3 = ch;
    ADC_CTRL2 |= 1u << 22;                /* OCSWTRG */
    for (uint32_t t = 0; !(ADC_STS & 2u); t++)
        if (t > 100000) return 0xFFFF;
    return ADC_ODT & 0xFFFF;
}

static uint32_t vdd_mv(void) {            /* the 1.2 V internal reference against the supply */
    uint32_t r = adc_read(17);
    return r && r < 4096 ? 1200u * 4095u / r : 0;
}

/* ---- the report ---- */

static uint32_t entry_ctrl, entry_cfg, hext_ok, hext_us;
__attribute__((unused)) static uint32_t rf_id = 0xFFFFFFFF, nor_id = 0xFFFFFFFF;
static uint32_t census_on = 1, sense_ok;

static void hext_probe(void) {            /* does the external crystal start? (left off after) */
    uint32_t t = now();
    CRM_CTRL |= 1u << 16;
    while (!(CRM_CTRL & (1u << 17)) && now() - t < HZ / 20) kick();
    hext_ok = (CRM_CTRL >> 17) & 1;
    hext_us = (now() - t) / (HZ / 1000000u);
    CRM_CTRL &= ~(1u << 16);
}

static void report(void) {
    puts_(BANNER " build=" BUILD_ID " board=" BOARD_NAME);
    nl();
    puts_("mcu");
    kv_hex("idcode", DBG_IDCODE, 8);
    kv_dec("flash_kb", FLASH_SIZE_KB);
    kv_hex("entry_crm_ctrl", entry_ctrl, 8);
    kv_hex("entry_crm_cfg", entry_cfg, 8);
    nl();
    puts_("clock sclk=hick_8mhz");
    kv_dec("hext_ok", hext_ok);
    kv_dec("hext_us", hext_us);
    nl();
    puts_("adc");
    kv_dec("vdd_mv", vdd_mv());
    kv_dec("temp_raw", adc_read(16));
#ifdef ADC_CHANNELS
    for (uint32_t i = 0; i < COUNT(adc_channels); i++) {
        putch(' ');
        putch('c');
        putch('h');
        dec(adc_channels[i]);
        putch('=');
        dec(adc_read(adc_channels[i]));
    }
#endif
    nl();
#ifdef RF_SCN
    rf_id = rf_read(0x00);
    puts_("rf");
    kv_hex("id", rf_id, 4);
    nl();
#else
    puts_("rf pins=unconfirmed");
    nl();
#endif
#ifdef NOR_CS
    nor_id = nor_jedec();
    puts_("nor");
    kv_hex("jedec", nor_id, 6);
    nl();
#else
    puts_("nor pins=unconfirmed");
    nl();
#endif
#ifdef LCD_CS
    puts_("lcd");
    kv_hex("rddid_raw", lcd_rddid, 8);
    kv_hex("rddst_raw", lcd_rddst, 8);
    nl();
#else
    puts_("lcd pins=unconfirmed");
    nl();
#endif
    uint32_t v[4];
    census_read(v);
    census_print(v);
#ifdef POWER_SENSE
    puts_("power");
    kv_dec("sense", gget(POWER_SENSE));
    kv_dec("auto_off", sense_ok);
    nl();
#endif
    puts_("serial");
    kv_dec("breaks", breaks);
    kv_dec("rx_errors", rx_errors);
    nl();
}

static void help(void) {
    puts_("keys: ? help, i report, r rf regs, l redraw, p census, O off, R reset");
#ifdef LCD_BL
    puts_(", b backlight");
#endif
#ifdef FLASHLIGHT
    puts_(", f flashlight");
#endif
    nl();
}

/* ---- the screen ---- */

#ifdef LCD_CS
static struct keys shown_keys = {~0u, ~0u, ~0u};

static void screen_static(void) {
    lcd_fill(0, 0, LCD_W, LCD_H, BLACK);
    lcd_fill(0, 0, LCD_W, 1, WHITE);      /* a frame on the outermost pixels: shows the offsets */
    lcd_fill(0, LCD_H - 1, LCD_W, 1, WHITE);
    lcd_fill(0, 0, 1, LCD_H, WHITE);
    lcd_fill(LCD_W - 1, 0, 1, LCD_H, WHITE);
    lcd_fill(1, 1, 6, 6, RED);            /* top left, as drawn: shows a flip or rotation */
    lcd_text(8, 4, "BLOWFANG UV21", AMBER);
    lcd_text(8, 16, "BRINGUP 2", WHITE);
    static const uint16_t bars[4] = {RED, GREEN, BLUE, WHITE};
    static const char names[4] = {'R', 'G', 'B', 'W'};
    for (uint32_t i = 0; i < 4; i++) {    /* labelled colour bars: red shown blue means BGR */
        lcd_fill(4 + i * 30, 30, 28, 20, bars[i]);
        lcd_char(4 + i * 30 + 10, 34, names[i], BLACK, bars[i]);
    }
    lcd_text(4, 56, "RF", GREY);
    lcd_hexat(44, 56, rf_id, 4, WHITE);
    lcd_text(4, 70, "NOR", GREY);
    lcd_hexat(44, 70, nor_id, 6, WHITE);
    lcd_text(4, 84, "LCD", GREY);
    lcd_hexat(44, 84, lcd_rddid, 8, WHITE);
    lcd_text(4, 98, "VDD", GREY);
    lcd_text(4, 112, "KEY", GREY);
    lcd_text(4, 126, "PTT", GREY);
    lcd_text(4, 140, "UP", GREY);
    shown_keys.matrix = shown_keys.side = shown_keys.direct = ~0u;
}

static void screen_live(struct keys keys, uint32_t secs) {
    lcd_decat(44, 98, vdd_mv(), WHITE);
    if (keys.matrix != shown_keys.matrix || keys.side != shown_keys.side ||
        keys.direct != shown_keys.direct) {
        const char *n = key_name(keys);
        lcd_text(44, 112, "        ", WHITE);
        if (n) lcd_text(44, 112, n, AMBER);
        else if (keys.matrix || keys.side || keys.direct) lcd_hexat(44, 112, keys.matrix | keys.side << 24, 8, AMBER);
        shown_keys = keys;
    }
    lcd_decat(44, 126, breaks, WHITE);
    lcd_decat(44, 140, secs, WHITE);
}
#endif

/* ---- backlight search: board.h's candidates, one at a time, then all ---- */

#ifdef BL_CANDIDATES
static const uint8_t bl_pins[] = {BL_CANDIDATES};
static const char *const bl_names[] = {BL_NAMES};

/* step n < count: candidate n alone, driven high; step count: all of them high */
static void bl_step(uint32_t n) {
    puts_("backlight step");
    kv_dec("n", n);
    for (uint32_t i = 0; i < COUNT(bl_pins); i++) {
        uint32_t on = n == i || n >= COUNT(bl_pins);
        if (on) gout(bl_pins[i], 1);
        else gmode(bl_pins[i], IN);       /* back to how the first image left it: undriven */
        putch(' ');
        puts_(bl_names[i]);
        putch('=');
        putch(on ? '1' : '0');
    }
    nl();
#ifdef LCD_CS
    lcd_fill(8, 16, LCD_W - 16, 12, BLACK);
    lcd_text(8, 16, "BL", GREY);
    for (uint32_t i = 0, x = 32; i < COUNT(bl_pins); i++)
        if (n == i || n >= COUNT(bl_pins)) {
            lcd_text(x, 16, bl_names[i], AMBER);
            x += 32;
        }
#endif
}
#endif

/* ---- start-up and the loop ---- */

static void reset_mcu(void) {
    tx_drain();
    SCB_AIRCR = 0x05FA0004u;              /* SYSRESETREQ: the bootloader runs again */
    for (;;) {}
}

static void power_off(void) {
    puts_("power off");
    nl();
    tx_drain();
    gset(POWER_LATCH, 0);
    for (;;) kick();                      /* off once the switch is off; on a supply, stays here */
}

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss;

__attribute__((noreturn)) void reset(void);
void reset(void) {
    SCB_VTOR = 0x08001000;
    uint32_t ec = CRM_CTRL, eg = CRM_CFG;  /* how the bootloader left the clocks */
    for (uint32_t *s = &_sidata, *d = &_sdata; d < &_edata;) *d++ = *s++;
    for (uint32_t *d = &_sbss; d < &_ebss;) *d++ = 0;
    entry_ctrl = ec;
    entry_cfg = eg;

    CRM_AHBEN |= (1u << 17) | (1u << 18) | (1u << 19) | (1u << 22);   /* GPIO A, B, C, F */
    gout(POWER_LATCH, 1);                 /* hold the power on */
    CRM_CFG = 0;                          /* the internal 8 MHz clock, no dividers */
    while (CRM_CFG & (3u << 2)) {}
    CRM_CTRL &= ~(1u << 24);              /* PLL off (the bootloader may have used it) */
    SYST_RVR = 0xFFFFFF;
    SYST_CVR = 0;
    SYST_CSR = 5;                         /* processor clock, on, no interrupt */
    last_cvr = SYST_CVR;

    CRM_APB2EN |= 1u << 14;               /* USART1 */
    gaf(UART_TX, UART_AF);
    gpull(UART_RX, UP);
    gaf(UART_RX, UART_AF);
    U_BAUDR = HZ / 115200;
    U_CTRL1 = (1u << 13) | (1u << 3) | (1u << 2);
    puts_(BANNER);
    nl();

    hext_probe();
    adc_init();
#ifdef RF_SCN
    rf_init();
#endif
#ifdef LCD_BL
    gout(LCD_BL, 1);
#endif
#ifdef FLASHLIGHT
    gout(FLASHLIGHT, 0);
#endif
#ifdef POWER_SENSE
    gmode(POWER_SENSE, IN);
    delay_us(100);
    /* it can't read "off" while we run with the switch on: if it does, the polarity in board.h is
       wrong, so never act on it (a wrong polarity would switch the radio off at every boot) */
    sense_ok = !gget(POWER_SENSE);
#endif
    keys_init();
#ifdef LCD_CS
    lcd_init();
#endif
    report();
    help();
#ifdef LCD_CS
    screen_static();
#endif
#ifdef BL_CANDIDATES
    bl_step(0);
#endif

    uint32_t last_tick = now(), ticks = 0, prev = 0, seen[4], shown[4], same = 0, off_ms = 0;
    struct keys keys = keys_scan(), keys_shown = keys;
    census_read(shown);
    for (;;) {
        kick();
        int c = getch();
        if (c == -2) {
            puts_("ptt break");
            kv_dec("n", breaks);
            nl();
        } else if (c >= 0) {
            if (prev == 0xAA && c == 0x42) {
                puts_("bootloader: command 42 seen, resetting");
                nl();
                reset_mcu();
            }
            prev = (uint32_t)c;
            switch (c) {
            case '?': help(); break;
            case 'i': report(); break;
#ifdef RF_SCN
            case 'r': rf_regs(); break;
#endif
#ifdef LCD_CS
            case 'l': lcd_init(); screen_static(); break;
#endif
#ifdef LCD_BL
            case 'b': gset(LCD_BL, !((REG(gbase(LCD_BL) + 0x14) >> (LCD_BL & 15)) & 1)); break;
#endif
#ifdef FLASHLIGHT
            case 'f': gset(FLASHLIGHT, !((REG(gbase(FLASHLIGHT) + 0x14) >> (FLASHLIGHT & 15)) & 1)); break;
#endif
            case 'p':
                census_on ^= 1;
                puts_("census");
                kv_dec("on", census_on);
                nl();
                break;
            case 'O': power_off(); break;
            case 'R': puts_("reset"); nl(); reset_mcu(); break;
            default: break;
            }
        }
        if (now() - last_tick < HZ / 50) continue;   /* the rest every 20 ms */
        last_tick += HZ / 50;
        struct keys k = keys_scan();
        if (keys_same(k, keys) && !keys_same(k, keys_shown)) {        /* two scans alike */
            puts_("keys");
            kv_hex("matrix", k.matrix, 8);
            kv_hex("side", k.side, 1);
            kv_hex("direct", k.direct, 2);
            const char *n = key_name(k);
            if (n) {
                puts_(" name=");
                puts_(n);
            }
            nl();
            keys_shown = k;
        }
        keys = k;
        census_read(seen);
        int diff = 0;
        for (int i = 0; i < 4; i++) diff |= seen[i] != shown[i];
        same = diff ? same + 1 : 0;
        if (same >= 3) {                  /* steady for 60 ms: a level, not noise */
            if (census_on) census_print(seen);
            for (int i = 0; i < 4; i++) shown[i] = seen[i];
            same = 0;
        }
#ifdef POWER_SENSE
        if (sense_ok) {                   /* off for 200 ms: drop the latch */
            off_ms = gget(POWER_SENSE) ? off_ms + 20 : 0;
            if (off_ms >= 200) power_off();
        }
#else
        (void)off_ms;
#endif
        if (++ticks % 50 == 0) {
#ifdef BL_CANDIDATES
            if (ticks % 500 == 0 && ticks / 500 <= COUNT(bl_pins)) bl_step(ticks / 500);
#endif
#ifdef LCD_CS
            screen_live(keys_shown, ticks / 50);
#endif
        }
    }
}

static void fault(void) {
    puts_("fault");
    nl();
    for (;;) kick();
}

__attribute__((section(".vectors"), used)) static const void *const vectors[16] = {
    (void *)0x20004000, (void *)reset, (void *)fault, (void *)fault,
    (void *)fault, (void *)fault, (void *)fault, 0, 0, 0, 0,
    (void *)fault, (void *)fault, 0, (void *)fault, (void *)fault,
};
