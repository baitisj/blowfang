/* UV-21 Pro dumper: one 1 KB page of blowfang that reads the radio out over the K-plug serial line.
 *
 * Why it exists: the AT32F421's Flash Access Protection lets only code running from the radio's own
 * flash read that flash, so the stock firmware can't be backed up from outside. Baofeng's
 * bootloader writes an update one 1 KB page at a time and erases only the pages it writes, so this
 * image (page 0 of the 60 KB app area) costs exactly one page of the stock app, which
 * radios/uv21/README.md shows how to rebuild.
 *
 * It only reads. Nothing here writes or erases flash (no FLASH register is touched), writes the SPI
 * flash (only JEDEC ID 0x9F and READ 0x03 are sent), or touches the RF chip (its bus pins stay
 * inputs: which chip it is comes later, from the dumped firmware and a bring-up image). The PTT
 * line (PA10) is the serial receive pin and is read as serial data only.
 *
 * Board facts (the UV-5RM's 5RHNRFB-YP07 board, from its open firmware's pin map; the UV-21 Pro's
 * board is UV21NRFB-YP07): PF6 power self-latch (high = stay on), USART1 TX PA9 / RX PA10 (AF1),
 * SPI-NOR on SPI1 PA5/PA6/PA7 (AF0) with CS PA8. PA13/PA14 (SWD) are left as SWD.
 *
 * Output: frames A5 5A, type, addr (4, little-endian), len (2, little-endian, <= 256), data,
 * CRC-16/XMODEM (2, big-endian) over type..data. Types: 'B' banner (text, addr 0), 'F' MCU memory at
 * addr, 'J' SPI JEDEC ID (in addr, no data), 'S' SPI flash at offset addr, 'E' end of pass
 * (addr = pass number). Two passes at boot, then one
 * more for each 'D' received. tools/uv21_kdh.py dump reads it.
 *
 * SPDX-License-Identifier: MIT */
#include <stdint.h>

#define REG(a) (*(volatile uint32_t *)(a))
#define CRM_CFG REG(0x40021004)
#define CRM_AHBEN REG(0x40021014)
#define CRM_APB2EN REG(0x40021018)
#define GPIO(port, off) REG(0x48000000 + (port) * 0x400 + (off))
#define PA 0
#define PF 5
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

static const char banner[] = "blowfang uv21 dumper 1";

static uint8_t text(uint32_t a) { return (uint8_t)banner[a]; }

static void pin(uint32_t port, uint32_t bit, uint32_t high) {
    GPIO(port, high ? SCR : CLR) = 1u << bit;
}

static void pass(uint32_t n) {
    uint32_t kb = FLASH_SIZE_KB;
    if (kb < 16 || kb > 256) kb = 64;
    out('F', 0x08000000, kb * 1024, mem);     /* bootloader + app (this page included) */
    out('F', 0x1FFFE400, 0x1000, mem);        /* Baofeng's region 2 (code and data) */
    out('F', 0x1FFFF7E0, 0x14, mem);          /* flash size, unique ID */
    out('F', 0x1FFFF800, 0x10, mem);          /* user system data (option bytes) */
    pin(PA, 8, 0);                            /* SPI flash: JEDEC ID */
    spi(0x9F);
    uint8_t id[3];
    for (int i = 0; i < 3; i++) id[i] = spi(0);
    pin(PA, 8, 1);
    out('J', id[0] | id[1] << 8 | (uint32_t)id[2] << 16, 0, spi0);
    uint32_t cap = id[2];                     /* JEDEC capacity code: 2^cap bytes */
    if (cap < 0x11 || cap > 0x18) cap = 0x15; /* unknown: 2 MB, the 5RM's XM25QH16 */
    pin(PA, 8, 0);
    for (int i = 0; i < 4; i++) spi(i ? 0 : 0x03);   /* READ from 0 */
    out('S', 0, 1u << cap, spi0);
    pin(PA, 8, 1);
    out('E', n, 0, spi0);
}


void reset(void) {
    SCB_VTOR = 0x08001000;
    CRM_AHBEN |= (1u << 17) | (1u << 22);     /* GPIOA, GPIOF */
    GPIO(PF, CFGR) = 1u << 12;                /* PF6 output (PF0/PF1 stay crystal pins) */
    GPIO(PF, SCR) = 1u << 6;                  /* hold the power on */
    CRM_CFG = 0;                              /* run on the 8 MHz internal clock, no dividers */
    while (CRM_CFG & (3u << 2)) {}
    CRM_APB2EN |= (1u << 12) | (1u << 14);    /* SPI1, USART1 */
    /* whole-register writes from the reset values: PA13/PA14 stay SWD (AF, their pulls kept);
       PA8 out, PA5-PA7 AF0 (SPI1), PA9/PA10 AF1 (USART1), PA10 pulled up; the RF chip's bus
       pins (PA2, PA3, PC13) stay inputs, so the RF chip is never driven at all */
    GPIO(PA, CFGR) = 0x28000000u | 0x0029A800u;
    GPIO(PA, PULL) = 0x24000000u | (1u << 20);
    GPIO(PA, MUXH) = 0x110u;
    GPIO(PA, SCR) = 1u << 8;                  /* SPI CS idle high */
    S_CTRL1 = (1u << 9) | (1u << 8) | (4u << 3) | (1u << 2);   /* master, mode 0, /32, soft CS */
    S_CTRL1 |= 1u << 6;
    U_BAUDR = 69;                             /* 8 MHz / 115200 */
    U_CTRL1 = (1u << 13) | (1u << 3) | (1u << 2);
    out('B', 0, sizeof banner - 1, text);
    uint32_t n = 1;
    pass(n++);
    pass(n++);
    for (;;) {
        kick();
        if ((U_STS & (1u << 5)) && (uint8_t)U_DT == 'D') pass(n++);
    }
}

static void fault(void) {
    for (;;) kick();
}

__attribute__((section(".vectors"), used)) static const void *const vectors[] = {
    (void *)0x20004000, (void *)reset, (void *)fault, (void *)fault,
};
