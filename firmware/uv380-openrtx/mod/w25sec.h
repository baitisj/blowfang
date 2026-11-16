/* w25sec.h: read the radio's calibration from a RAM module, read only. The MD-UV380 keeps its
   factory calibration in the W25Q128's security registers 1 and 2 (flash addresses 0x1000-0x10FF,
   0x2000-0x20FF), which OpenRTX reads at boot (nvmem_MDx.c) and a module can't reach. This reads
   them with the flash's "read security register" command (0x48) on SPI1 as OpenRTX set it up,
   CS on PD7. Nothing here can write or erase: 0x48 is the only command it sends.

   Sharing SPI1 with OpenRTX (settings, codeplug): as c6spi.h, a transaction runs with interrupts
   off and only while CS is high and SPI1 idle, else it sleeps 1 ms and tries again.

   Table layout (RadioFoundry docs/spec/hardware-dm1701.md 12.2, the same 512 bytes on the
   MD-UV380; nvmem_MDx.c reads the same offsets): register 2 offset 0x30 UHF DMR I gain (C6000
   0x45), 0x40 UHF DMR Q gain (0x46), 0x70 / 0x80 the FM pair, 9 points from 400 MHz in 10 MHz
   steps; register 1 offset 0x09 the UHF reference oscillator tune.
   SPDX-License-Identifier: MIT */
#ifndef W25SEC_H
#define W25SEC_H

#include "ortx_mod.h"

#define W25_FN static __attribute__((unused))

#define W25_REG(a) (*(volatile uint32_t *)(a))
#define W25_SPI1 0x40013000u            /* CR1, CR2, SR, DR */
#define W25_GPIOD 0x40020C00u
#define W25_CS 7u
#define W25_CHUNK 32u

W25_FN inline uint32_t w25_irq_off(void) {
    uint32_t m;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(m)::"memory");
    return m;
}

W25_FN inline void w25_irq_on(uint32_t m) {
    __asm volatile("msr primask, %0" ::"r"(m) : "memory");
}

W25_FN uint8_t w25_byte(uint8_t v) {
    W25_REG(W25_SPI1 + 0x0Cu) = v;
    unsigned t = 0;
    while ((W25_REG(W25_SPI1 + 0x08u) & 1u) == 0 && ++t < 100000u) {
    }
    return (uint8_t)W25_REG(W25_SPI1 + 0x0Cu);
}

/* n bytes of security register memory at flash address a (0x1000-0x30FF, never across a
   register's 256 bytes): 0, or -1 (SPI1 off or busy for 50 ms) */
W25_FN int w25_sec_read(const ortx_api_t *api, uint32_t a, uint8_t *buf, unsigned n) {
    if ((W25_REG(W25_SPI1 + 0x00u) & (1u << 6)) == 0)     /* SPE: OpenRTX never set it up */
        return -1;
    unsigned waits = 0;
    for (unsigned o = 0; o < n;) {
        unsigned k = n - o < W25_CHUNK ? n - o : W25_CHUNK;
        uint32_t m = w25_irq_off();
        int busy = ((W25_REG(W25_GPIOD + 0x14u) >> W25_CS) & 1u) == 0 ||
                   (W25_REG(W25_SPI1 + 0x08u) & (1u << 7)) != 0;
        if (busy) {
            w25_irq_on(m);
            if (++waits > 50u)
                return -1;
            api->sleep_ms(1);
            continue;
        }
        while (W25_REG(W25_SPI1 + 0x08u) & 1u)              /* stale RXNE */
            (void)W25_REG(W25_SPI1 + 0x0Cu);
        uint32_t x = a + o;
        W25_REG(W25_GPIOD + 0x18u) = 1u << (W25_CS + 16u);
        w25_byte(0x48);
        w25_byte((uint8_t)(x >> 16));
        w25_byte((uint8_t)(x >> 8));
        w25_byte((uint8_t)x);
        w25_byte(0);                                         /* dummy */
        for (unsigned i = 0; i < k; i++)
            buf[o + i] = w25_byte(0);
        W25_REG(W25_GPIOD + 0x18u) = 1u << W25_CS;
        w25_irq_on(m);
        o += k;
    }
    return 0;
}

/* a 9-point UHF row (400-480 MHz, 10 MHz apart) at f_khz, linear between points, as OpenRTX's
   interpCalParameter does; clamped to the row's ends */
W25_FN unsigned w25_uhf_interp(const uint8_t *row9, uint32_t f_khz) {
    if (f_khz <= 400000u)
        return row9[0];
    uint32_t d = f_khz - 400000u, i = d / 10000u, r = d % 10000u;
    if (i >= 8u)
        return row9[8];
    int a = row9[i], b = row9[i + 1];
    return (unsigned)(a + ((b - a) * (int)r) / 10000);
}

/* the DMR modulation pair at f_khz from the table: *i_gain (0x45), *q_gain (0x46); 0, or -1
   (unreadable, or values that can't be a calibration: 0x00 / 0xFF rows) */
W25_FN int w25_dmr_pair(const ortx_api_t *api, uint32_t f_khz, unsigned *i_gain, unsigned *q_gain) {
    uint8_t ir[9], qr[9];
    if (w25_sec_read(api, 0x2030u, ir, 9) || w25_sec_read(api, 0x2040u, qr, 9))
        return -1;
    for (unsigned k = 0; k < 9; k++)
        if (ir[k] == 0 || ir[k] == 0xFF || qr[k] == 0 || qr[k] == 0xFF)
            return -1;
    *i_gain = w25_uhf_interp(ir, f_khz);
    *q_gain = w25_uhf_interp(qr, f_khz);
    return 0;
}

#endif
