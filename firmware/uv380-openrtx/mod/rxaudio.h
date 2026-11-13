/* The UV-380's receive audio into a module with no hardware mod (c6listen.c, the 11-13 14:52Z
   tone run): HR_C6000 register 26 at fd and RX_AUDIO_MUX (PD9) high route the FM receiver's
   audio to the codec, whose I2S port SPI3 takes as a slave with 32-bit channels (P32). DMA1
   stream 0 fills a ring of halfwords, circular; each sample comes twice a halfword apart, so
   every other halfword is the audio. rx_start puts the route and the DMA on and keeps what it
   found, rx_stop puts it all back. For modules that #include it (static functions, no state).
   SPDX-License-Identifier: MIT */
#ifndef RXAUDIO_H
#define RXAUDIO_H

#include "ortx_mod.h"

#define RXA_REG(a) (*(volatile uint32_t *)(a))
#define RXA_RCC_AHB1ENR 0x40023830u
#define RXA_RCC_APB1RSTR 0x40023820u
#define RXA_RCC_APB1ENR 0x40023840u
#define RXA_SPI3 0x40003C00u
#define RXA_SPI_CR2 0x04u
#define RXA_SPI_DR 0x0Cu
#define RXA_SPI_I2SCFGR 0x1Cu
#define RXA_SPI_I2SPR 0x20u
#define RXA_DMA1_LIFCR 0x40026008u
#define RXA_S0 0x40026010u        /* DMA1 stream 0: CR, NDTR, PAR, M0AR, M1AR, FCR */
#define RXA_GPIOA 0x40020000u
#define RXA_GPIOC 0x40020800u
#define RXA_GPIOD 0x40020C00u
#define RXA_MUX_PIN 9u

typedef struct {
    uint32_t moder, pupdr, afr;
} rxa_pin;

typedef struct {
    uint32_t dma[6];
    rxa_pin ws, ck, sd;
    uint32_t mux;
    uint8_t r26;
    unsigned ring;            /* halfwords */
    volatile int16_t *buf;
} rxa_state;

/* a pin to alternate function af (no pull), its old settings into *s */
static void rxa_pin_af(uint32_t port, unsigned pin, unsigned af, rxa_pin *s) {
    uint32_t afr = port + (pin < 8 ? 0x20u : 0x24u), sh = (pin & 7u) * 4u;
    s->moder = RXA_REG(port), s->pupdr = RXA_REG(port + 0x0Cu), s->afr = RXA_REG(afr);
    RXA_REG(port + 0x0Cu) = s->pupdr & ~(3u << (2 * pin));
    RXA_REG(afr) = (s->afr & ~(15u << sh)) | (af << sh);
    RXA_REG(port) = (s->moder & ~(3u << (2 * pin))) | (2u << (2 * pin));
}

static void rxa_pin_back(uint32_t port, unsigned pin, const rxa_pin *s) {
    RXA_REG(port) = s->moder;
    RXA_REG(port + 0x0Cu) = s->pupdr;
    RXA_REG(port + (pin < 8 ? 0x20u : 0x24u)) = s->afr;
}

static void rxa_dma_off(void) {
    RXA_REG(RXA_S0) &= ~1u;
    for (unsigned t = 0; (RXA_REG(RXA_S0) & 1u) && t < 100000u; t++) {
    }
    RXA_REG(RXA_DMA1_LIFCR) = 0x3Du;
}

/* ring: n halfwords (a power of 2) in DMA-capable RAM (the module's scratch) */
static void rx_start(const ortx_api_t *api, rxa_state *s, volatile int16_t *ring, unsigned n) {
    s->ring = n, s->buf = ring;
    s->r26 = api->c6000_read(0x26);
    s->mux = (RXA_REG(RXA_GPIOD + 0x14u) >> RXA_MUX_PIN) & 1u;      /* ODR */
    api->c6000_write(0x26, 0xFD);
    RXA_REG(RXA_GPIOD + 0x18u) = 1u << RXA_MUX_PIN;
    RXA_REG(RXA_RCC_AHB1ENR) |= 1u << 21;
    for (unsigned i = 0; i < 6; i++)
        s->dma[i] = RXA_REG(RXA_S0 + 4u * i);
    rxa_dma_off();
    RXA_REG(RXA_RCC_APB1ENR) |= 1u << 15;
    RXA_REG(RXA_RCC_APB1RSTR) |= 1u << 15;
    RXA_REG(RXA_RCC_APB1RSTR) &= ~(1u << 15);
    rxa_pin_af(RXA_GPIOA, 15, 6, &s->ws);
    rxa_pin_af(RXA_GPIOC, 10, 6, &s->ck);
    rxa_pin_af(RXA_GPIOC, 12, 6, &s->sd);
    RXA_REG(RXA_S0 + 8u) = RXA_SPI3 + RXA_SPI_DR;
    RXA_REG(RXA_S0 + 12u) = (uint32_t)(uintptr_t)ring;
    RXA_REG(RXA_S0 + 4u) = n;
    RXA_REG(RXA_S0 + 20u) = 0;
    /* CHSEL 0, PL high, 16-bit memory and peripheral, MINC, CIRC, then EN */
    RXA_REG(RXA_S0) = (2u << 16) | (1u << 13) | (1u << 11) | (1u << 10) | (1u << 8);
    RXA_REG(RXA_S0) |= 1u;
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SPR) = 2u;
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SCFGR) = (1u << 11) | (1u << 8) | 1u;   /* I2SMOD, slave rx, CHLEN 32 */
    RXA_REG(RXA_SPI3 + RXA_SPI_CR2) = 1u;                                /* RXDMAEN */
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SCFGR) |= 1u << 10;                     /* I2SE */
}

/* the DMA's write position in the ring (halfwords) */
static unsigned rx_wr(const rxa_state *s) {
    return (s->ring - RXA_REG(RXA_S0 + 4u)) & (s->ring - 1u);
}

static void rx_stop(const ortx_api_t *api, rxa_state *s) {
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SCFGR) &= ~(1u << 10);
    RXA_REG(RXA_SPI3 + RXA_SPI_CR2) = 0;
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SCFGR) = 0;
    rxa_dma_off();
    for (unsigned i = 1; i < 6; i++)
        RXA_REG(RXA_S0 + 4u * i) = s->dma[i];
    RXA_REG(RXA_S0) = s->dma[0] & ~1u;
    rxa_pin_back(RXA_GPIOA, 15, &s->ws);
    rxa_pin_back(RXA_GPIOC, 10, &s->ck);
    rxa_pin_back(RXA_GPIOC, 12, &s->sd);
    RXA_REG(RXA_RCC_APB1RSTR) |= 1u << 15;
    RXA_REG(RXA_RCC_APB1RSTR) &= ~(1u << 15);
    RXA_REG(RXA_RCC_APB1ENR) &= ~(1u << 15);
    api->c6000_write(0x26, s->r26);
    RXA_REG(RXA_GPIOD + 0x18u) = s->mux ? 1u << RXA_MUX_PIN : 1u << (RXA_MUX_PIN + 16);
}

/* halfwords the DMA wrote in `ms` ms, timed from one ms tick to another (sleeping between
   reads, which come well within the ring's time); *took: the ms it covered */
__attribute__((unused)) static uint32_t rx_count(const ortx_api_t *api, const rxa_state *s, uint32_t ms, uint32_t *took) {
    uint32_t t0 = api->ms(), t;
    while ((t = api->ms()) == t0) {
    }
    unsigned last = rx_wr(s);
    uint32_t total = 0, end = t + ms;
    t0 = t;
    for (;;) {
        if ((int32_t)(api->ms() - end) < -4)
            api->sleep_ms(2);
        else
            while ((int32_t)(api->ms() - end) < 0) {
            }
        unsigned w = rx_wr(s);
        total += (w - last) & (s->ring - 1u);
        last = w;
        t = api->ms();
        if ((int32_t)(t - end) >= 0)
            break;
    }
    *took = t - t0;
    return total;
}

#endif
