/* UV-21 Pro dumper: one 1 KB page of blowfang that reads the radio out over the K-plug serial line.
 *
 * Why it exists: the AT32F421's Flash Access Protection lets only code running from the radio's own
 * flash read that flash, so the stock firmware can't be backed up from outside. Baofeng's
 * bootloader writes an update one 1 KB page at a time and erases only the pages it writes, so this
 * image (page 0 of the 60 KB app area) costs exactly one page of the stock app, which
 * radios/uv21/README.md shows how to rebuild.
 *
 * It only reads. Nothing here writes or erases flash (no FLASH register is touched), writes the SPI
 * flash (only RELEASE FROM POWER-DOWN 0xAB, JEDEC ID 0x9F and READ 0x03 are sent), or touches the RF
 * chip (its bus pins PB3/PB5/PB6 stay inputs). The PTT line (PA10) is the serial receive pin and is
 * read as serial data only.
 *
 * Board facts (UV21NRFB-YP07, read from its stock firmware on 2026-11-23; dumper 1 used the UV-5RM's
 * pins and read the SPI flash as all FF): USART1 TX PA9 / RX PA10 (AF1); SPI-NOR on SPI1 PA5/PA6/PA7
 * (AF0), mode 3, CS PA2; PA8 is the power self-latch (held low here, so the power switch still turns
 * the radio off); PF6 is a keypad row (left alone). PA13/PA14 (SWD) are left as SWD.
 *
 * Output: frames A5 5A, type, addr (4, little-endian), len (2, little-endian, <= 256), data,
 * CRC-16/XMODEM (2, big-endian) over type..data. Types: 'B' banner (text, addr 0), 'T' SPI taste
 * (addr = SPI mode | JEDEC ID << 8, data = the first 256 bytes, one frame per mode tried), 'F' MCU
 * memory at addr, 'J' SPI JEDEC ID (in addr, no data), 'S' SPI flash at offset addr, 'E' end
 * (addr = pass number, 0 for a taste). At boot: the banner and a taste. 'T' received: another
 * taste; 'D' received: a full pass (MCU memory, a taste, the whole SPI flash in the first mode whose
 * JEDEC ID looked real). tools/uv21_kdh.py dump reads it.
 *
 * SPDX-License-Identifier: MIT */
#include <stdint.h>

#define REG(a) (*(volatile uint32_t *)(a))
#define CRM_CFG REG(0x40021004)
#define CRM_AHBEN REG(0x40021014)
#define CRM_APB2EN REG(0x40021018)
#define GPIO(port, off) REG(0x48000000 + (port) * 0x400 + (off))
#define PA 0
#define CFGR 0x00
#define PULL 0x0C
#define SCR 0x18
#define MUXH 0x24
#define CLR 0x28
#define U_STS REG(0x40013800)
#define U_DT REG(0x40013804)
#define U_BAUDR REG(0x40013808)
#define U_CTRL1 REG(0x4001380C)
#define S_CTRL1 REG(0x40013000)
#define S_STS REG(0x40013008)
#define S_DT REG(0x4001300C)
#define WDT_CMD REG(0x40003000)
#define SCB_VTOR REG(0xE000ED08)

#define FLASH_SIZE_KB (*(volatile uint16_t *)0x1FFFF7E0)

static uint16_t crc;

static void kick(void) { WDT_CMD = 0xAAAA; }   /* reload, in case the bootloader started the watchdog */

static void tx(uint8_t b) {
    while (!(U_STS & (1u << 7))) {}           /* TDBE */
    U_DT = b;
}

static void txc(uint8_t b) {                  /* send and add to the CRC-16/XMODEM */
    crc ^= (uint16_t)b << 8;
    for (int i = 0; i < 8; i++)
        crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    tx(b);
}

/* one or more frames: n bytes from get(addr), get(addr + 1), ... (n = 0: one empty frame) */
static void out(char type, uint32_t addr, uint32_t n, uint8_t (*get)(uint32_t)) {
    do {
        uint32_t k = n > 256 ? 256 : n;
        tx(0xA5);
        tx(0x5A);
        crc = 0;
        txc((uint8_t)type);
        for (int i = 0; i < 32; i += 8) txc((uint8_t)(addr >> i));
        txc((uint8_t)k);
        txc((uint8_t)(k >> 8));
        for (uint32_t i = 0; i < k; i++) txc(get(addr + i));
        uint16_t c = crc;
        tx((uint8_t)(c >> 8));
        tx((uint8_t)c);
        kick();
        addr += k;
        n -= k;
    } while (n);
}

static uint8_t mem(uint32_t a) { return *(volatile uint8_t *)a; }   /* code in flash may read flash */

static uint8_t spi(uint32_t b) {
    while (!(S_STS & 2u)) {}                  /* TDBE */
    S_DT = (uint8_t)b;
    while (!(S_STS & 1u)) {}                  /* RDBF */
    return (uint8_t)S_DT;
}

static uint8_t spi0(uint32_t a) { (void)a; return spi(0); }

static const char banner[] = "blowfang uv21 dumper 2";

static uint8_t text(uint32_t a) { return (uint8_t)banner[a]; }

static void cs(uint32_t sel) { GPIO(PA, sel ? CLR : SCR) = 1u << 2; }   /* CS PA2, active low */

#define S_BASE ((1u << 9) | (1u << 8) | (4u << 3) | (1u << 2))   /* soft CS, /32, master */

static uint32_t jedec(uint32_t mode) {
    S_CTRL1 = S_BASE | mode;                  /* CPOL/CPHA change only while disabled */
    S_CTRL1 = S_BASE | mode | (1u << 6);
    cs(1);
    spi(0xAB);                                /* release from deep power-down (stock sends B9 too) */
    cs(0);
    for (volatile int i = 0; i < 400; i++) {} /* tRES1, 3..30 us */
    cs(1);
    spi(0x9F);
    uint32_t id = 0;
    for (int i = 0; i < 24; i += 8) id |= (uint32_t)spi(0) << i;
    cs(0);
    return id;
}

static void read_from0(void) {
    cs(1);
    for (int i = 0; i < 4; i++) spi(i ? 0 : 0x03);   /* READ from 0; CS stays low */
}

static uint32_t best;                         /* the SPI mode the full read uses */

static void taste(void) {
    best = 4;
    for (uint32_t mode = 3; mode < 4; mode -= 3) {   /* modes 3 (stock) and 0 */
        uint32_t id = jedec(mode);
        if (best == 4 && id && id != 0xFFFFFF) best = mode;
        read_from0();
        out('T', mode | id << 8, 256, spi0);
        cs(0);
    }
    if (best == 4) best = 3;
}

static void pass(uint32_t n) {
    uint32_t kb = FLASH_SIZE_KB;
    if (kb < 16 || kb > 256) kb = 64;
    out('F', 0x08000000, kb * 1024, mem);     /* bootloader + app (this page included) */
    out('F', 0x1FFFE400, 0x1000, mem);        /* Baofeng's region 2 (code and data) */
    out('F', 0x1FFFF7E0, 0x14, mem);          /* flash size, unique ID */
    out('F', 0x1FFFF800, 0x10, mem);          /* user system data (option bytes) */
    taste();
    uint32_t id = jedec(best);
    out('J', id, 0, spi0);
    uint32_t cap = id >> 16 & 0xFF;           /* JEDEC capacity code: 2^cap bytes */
    if (cap < 0x11 || cap > 0x18) cap = 0x15; /* unknown: 2 MB */
    read_from0();
    out('S', 0, 1u << cap, spi0);
    cs(0);
    out('E', n, 0, spi0);
}


void reset(void) {
    SCB_VTOR = 0x08001000;
    CRM_AHBEN |= 1u << 17;                    /* GPIOA */
    CRM_CFG = 0;                              /* run on the 8 MHz internal clock, no dividers */
    while (CRM_CFG & (3u << 2)) {}
    CRM_APB2EN |= (1u << 12) | (1u << 14);    /* SPI1, USART1 */
    /* whole-register writes from the reset values: PA13/PA14 stay SWD (AF, their pulls kept);
       PA2 out (CS, high first), PA8 out low (power latch released), PA5-PA7 AF0 (SPI1),
       PA9/PA10 AF1 (USART1), PA10 pulled up; everything else stays an input */
    GPIO(PA, SCR) = 1u << 2;
    GPIO(PA, CFGR) = 0x28000000u | 0x0029A810u;
    GPIO(PA, PULL) = 0x24000000u | (1u << 20);
    GPIO(PA, MUXH) = 0x110u;
    U_BAUDR = 69;                             /* 8 MHz / 115200 */
    U_CTRL1 = (1u << 13) | (1u << 3) | (1u << 2);
    out('B', 0, sizeof banner - 1, text);
    taste();
    out('E', 0, 0, spi0);
    uint32_t n = 1;
    for (;;) {
        kick();
        if (U_STS & (1u << 5)) {
            uint8_t c = (uint8_t)U_DT;
            if (c == 'D') pass(n++);
            if (c == 'T') {
                taste();
                out('E', 0, 0, spi0);
            }
        }
    }
}

static void fault(void) {
    for (;;) kick();
}

__attribute__((section(".vectors"), used)) static const void *const vectors[] = {
    (void *)0x20004000, (void *)reset, (void *)fault, (void *)fault,
};
