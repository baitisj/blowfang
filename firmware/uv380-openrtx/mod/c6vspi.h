/* c6vspi.h: the HR_C6000's voice SPI port (V_SPI) from a RAM module, bit-banged. A DMR radio's MCU
   hands the chip each voice burst's 27 bytes (page 3) here, a separate slave port from the U_SPI
   that c6spi.h drives (RadioFoundry docs/spec/modem-hrc6000.md 5, hardware-dm1701.md 4.3, MIT;
   the HR_C6000 manual 4.6.1): one operation per transaction, write = 0x03 0x00 then 27 bytes,
   read = 0x83 0x00 then 27 bytes clocked in; mode 2 (clock idle high, data taken on the falling
   edge), MSB first, at most 4 MHz.

   Pins: on the DM-1701 (the same TYT board family) V_SPI is SPI2 on PB13 (SCK), PB14 (MISO), PB15
   (MOSI) with PB12 the chip select; OpenRTX's MD-3x0 pin map names PB12 V_CS too. OpenRTX's
   MD-UV3x0 pin map uses none of PB12-PB15; that the TYT board wires them to V_SPI here too is
   what the dry check (c6vcheck.c) finds out: MISO read once with the pull-up and once with the
   pull-down tells a pin the chip drives from one left floating.

   vs_open takes PB12-PB15 if they are inputs or analog (nobody has claimed them), or if they are
   set up as V_SPI on SPI2 (PB12 an output, PB13-PB15 alternate function: modes a9, as the UV-380
   was found on 11-17) while SPI2 is idle: SPI and I2S disabled, DMA1 streams 3 and 4 (its RX and
   TX) off. Anything else is someone's and is refused. It makes them a bit-banged port: PB12 and
   PB13 push-pull outputs high (CS idle, clock idle), PB15 an output, PB14 an input; vs_close puts
   the mode, type, pull, speed and output registers back as they were (the alternate function
   numbers are never changed). Interrupts are off for each transaction (about 140 us for 27
   bytes) and while the mode registers change.

   Header only, no statics (RAM modules can't have writable ones).
   SPDX-License-Identifier: MIT */
#ifndef C6VSPI_H
#define C6VSPI_H

#include "ortx_mod.h"

#define VS_FN static __attribute__((unused))

#ifndef VS_REG
#define VS_REG(a) (*(volatile uint32_t *)(a))
#endif
#define VS_RCC_AHB1ENR 0x40023830u
#define VS_GPIOB 0x40020400u
#define VS_MODER 0x00u
#define VS_OTYPER 0x04u
#define VS_OSPEEDR 0x08u
#define VS_PUPDR 0x0Cu
#define VS_IDR 0x10u
#define VS_ODR 0x14u
#define VS_BSRR 0x18u
#define VS_CS 12u
#define VS_CLK 13u
#define VS_MISO 14u
#define VS_MOSI 15u
#define VS_MASK 0xFF000000u     /* MODER / PUPDR / OSPEEDR bits of PB12-PB15 */
#define VS_SPI_MODES 0xA9u      /* PB12 output, PB13-PB15 alternate function: V_SPI on SPI2 */
#define VS_SPI2 0x40003800u     /* CR1 bit 6 SPE, I2SCFGR (0x1C) bit 10 I2SE */
#define VS_DMA1 0x40026000u     /* stream x CR at 0x10 + 0x18 x, bit 0 EN */
#define VS_PAGE 3u
#define VS_N 27u

typedef struct {
    uint32_t moder, otyper, pupdr, ospeedr, odr, ahb1;
    int borrowed;               /* 1: taken from an idle SPI2 V_SPI set-up (modes a9), not free pins */
} vs_port;

#ifndef VS_HOST_TEST            /* tests/test_uv380_vspi.py runs vs_open / vs_close on the host */
VS_FN inline uint32_t vs_irq_off(void) {
    uint32_t m;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(m)::"memory");
    return m;
}

VS_FN inline void vs_irq_on(uint32_t m) {
    __asm volatile("msr primask, %0" ::"r"(m) : "memory");
}
#else
VS_FN inline uint32_t vs_irq_off(void) {
    return 0;
}

VS_FN inline void vs_irq_on(uint32_t m) {
    (void)m;
}
#endif

VS_FN inline void vs_wait(unsigned n) {
    for (volatile unsigned i = 0; i < n; i++) {
    }
}

/* PB12-PB15's mode bits as found (2 bits a pin, PB12 lowest), for a report */
VS_FN unsigned vs_modes(void) {
    return (VS_REG(VS_GPIOB + VS_MODER) & VS_MASK) >> 24;
}

/* SPI2 doing nothing: SPI and I2S off, DMA1 streams 3 and 4 off (registers read 0 with clocks off) */
VS_FN int vs_spi2_idle(void) {
    return !(VS_REG(VS_SPI2 + 0x00u) & (1u << 6)) && !(VS_REG(VS_SPI2 + 0x1Cu) & (1u << 10)) &&
           !(VS_REG(VS_DMA1 + 0x10u + 0x18u * 3u) & 1u) && !(VS_REG(VS_DMA1 + 0x10u + 0x18u * 4u) & 1u);
}

/* 0, or -1: a pin of PB12-PB15 is an output or alternate function (someone has claimed it) and
   they are not an idle SPI2 V_SPI set-up, nothing changed. Input (00) and analog (11) are free. */
VS_FN int vs_open(vs_port *p) {
    uint32_t m = vs_irq_off();
    p->ahb1 = VS_REG(VS_RCC_AHB1ENR);
    VS_REG(VS_RCC_AHB1ENR) = p->ahb1 | 2u;      /* GPIOB's clock (on already: the SPI flash) */
    p->moder = VS_REG(VS_GPIOB + VS_MODER);
    p->otyper = VS_REG(VS_GPIOB + VS_OTYPER);
    p->pupdr = VS_REG(VS_GPIOB + VS_PUPDR);
    p->ospeedr = VS_REG(VS_GPIOB + VS_OSPEEDR);
    p->odr = VS_REG(VS_GPIOB + VS_ODR);
    uint32_t md = p->moder >> 24;
    p->borrowed = ((md ^ (md >> 1)) & 0x55u) != 0;      /* a pin at 01 or 10 */
    if (p->borrowed && (md != VS_SPI_MODES || !vs_spi2_idle())) {
        if (!(p->ahb1 & 2u))
            VS_REG(VS_RCC_AHB1ENR) = p->ahb1;
        vs_irq_on(m);
        return -1;
    }
    VS_REG(VS_GPIOB + VS_BSRR) = (1u << VS_CS) | (1u << VS_CLK);
    VS_REG(VS_GPIOB + VS_OSPEEDR) = (p->ospeedr & ~VS_MASK) | (0x2u << (2u * VS_CS)) |
                                    (0x2u << (2u * VS_CLK)) | (0x2u << (2u * VS_MOSI));
    VS_REG(VS_GPIOB + VS_PUPDR) = p->pupdr & ~VS_MASK;
    VS_REG(VS_GPIOB + VS_OTYPER) = p->otyper & ~0xF000u;
    VS_REG(VS_GPIOB + VS_MODER) = (p->moder & ~VS_MASK) | (1u << (2u * VS_CS)) |
                                  (1u << (2u * VS_CLK)) | (1u << (2u * VS_MOSI));
    vs_irq_on(m);
    return 0;
}

VS_FN void vs_close(const vs_port *p) {
    uint32_t m = vs_irq_off();
    uint32_t hi = p->odr & 0xF000u;                 /* outputs first: PB12 goes back without a glitch */
    VS_REG(VS_GPIOB + VS_BSRR) = hi | ((~hi & 0xF000u) << 16);
    VS_REG(VS_GPIOB + VS_OTYPER) = (VS_REG(VS_GPIOB + VS_OTYPER) & ~0xF000u) | (p->otyper & 0xF000u);
    VS_REG(VS_GPIOB + VS_PUPDR) = (VS_REG(VS_GPIOB + VS_PUPDR) & ~VS_MASK) | (p->pupdr & VS_MASK);
    VS_REG(VS_GPIOB + VS_OSPEEDR) = (VS_REG(VS_GPIOB + VS_OSPEEDR) & ~VS_MASK) | (p->ospeedr & VS_MASK);
    VS_REG(VS_GPIOB + VS_MODER) = (VS_REG(VS_GPIOB + VS_MODER) & ~VS_MASK) | (p->moder & VS_MASK);
    if (!(p->ahb1 & 2u))
        VS_REG(VS_RCC_AHB1ENR) = VS_REG(VS_RCC_AHB1ENR) & ~2u;
    vs_irq_on(m);
}

/* MISO's pull while the port is open: 0 none, 1 up, 2 down (the floating test) */
VS_FN void vs_pull(unsigned pull) {
    uint32_t m = vs_irq_off();
    uint32_t v = VS_REG(VS_GPIOB + VS_PUPDR) & ~(3u << (2u * VS_MISO));
    VS_REG(VS_GPIOB + VS_PUPDR) = v | ((pull & 3u) << (2u * VS_MISO));
    vs_irq_on(m);
}

/* one byte out and in, MSB first, mode 2: MOSI set while the clock is high, the clock falls (both
   ends take the bit), rises (both shift); about 2 MHz as c6spi.h's */
VS_FN uint8_t vs_byte(uint8_t v) {
    uint8_t in = 0;
    for (unsigned b = 0; b < 8; b++) {
        VS_REG(VS_GPIOB + VS_BSRR) = (v & 0x80u) ? (1u << VS_MOSI) : (1u << (VS_MOSI + 16u));
        v = (uint8_t)(v << 1);
        vs_wait(8);
        VS_REG(VS_GPIOB + VS_BSRR) = 1u << (VS_CLK + 16u);
        in = (uint8_t)((in << 1) | ((VS_REG(VS_GPIOB + VS_IDR) >> VS_MISO) & 1u));
        vs_wait(8);
        VS_REG(VS_GPIOB + VS_BSRR) = 1u << VS_CLK;
    }
    return in;
}

/* one transaction on page 3 at address 0: n bytes out of `out` (NULL: 0xFF), into `in` (NULL:
   dropped), read when `read` */
VS_FN void vs_xfer(int read, const uint8_t *out, uint8_t *in, unsigned n) {
    uint32_t m = vs_irq_off();
    VS_REG(VS_GPIOB + VS_BSRR) = 1u << (VS_CS + 16u);
    vs_wait(8);
    vs_byte((uint8_t)((read ? 0x80u : 0u) | VS_PAGE));
    vs_byte(0x00u);
    for (unsigned i = 0; i < n; i++) {
        uint8_t v = vs_byte(out ? out[i] : 0xFFu);
        if (in)
            in[i] = v;
    }
    vs_wait(40);
    VS_REG(VS_GPIOB + VS_BSRR) = 1u << VS_CS;
    vs_wait(40);
    vs_irq_on(m);
}

/* the 27 bytes of a voice burst to the chip */
VS_FN void vs_write(const uint8_t *b) {
    vs_xfer(0, b, 0, VS_N);
}

VS_FN void vs_read(uint8_t *b) {
    vs_xfer(1, 0, b, VS_N);
}

#endif
