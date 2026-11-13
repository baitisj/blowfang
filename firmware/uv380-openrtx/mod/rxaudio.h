/* The UV-380's receive audio into a module with no hardware mod (c6listen.c, the 11-13 14:52Z
   tone run): HR_C6000 register 26 at fd and RX_AUDIO_MUX (PD9) high route the FM receiver's
   audio to the codec, whose I2S port SPI3 takes as a slave with 32-bit channels (P32). DMA1
   stream 0 fills a ring of halfwords, circular; each sample comes twice a halfword apart, so
   every other halfword is the audio, once the slave is in step with the codec's frames: rx_start
   enables it on a WS edge and checks the samples, trying again until they look right. It puts
   the route and the DMA on and keeps what it found; rx_stop puts it all back. For modules that #include it (static functions, no state).
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
    uint8_t r26, route;       /* route: rx_start set 26 and PD9 (rx_stop puts them back) */
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

/* SPI3 reset and set up as the I2S slave receiver (I2SMOD, slave rx, CHLEN 32, RXDMAEN), then
   enabled just after WS (PA15) comes to `level`: started at any other moment, the slave takes
   its 32-bit slots across the codec's frames (11-13 15:41Z: every halfword a sample's last bit
   or two, then fill) */
static void rxa_i2s_on(unsigned level) {
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SCFGR) = 0;
    RXA_REG(RXA_RCC_APB1RSTR) |= 1u << 15;
    RXA_REG(RXA_RCC_APB1RSTR) &= ~(1u << 15);
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SPR) = 2u;
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SCFGR) = (1u << 11) | (1u << 8) | 1u;
    RXA_REG(RXA_SPI3 + RXA_SPI_CR2) = 1u;
    unsigned t = 0;
    while (((RXA_REG(RXA_GPIOA + 0x10u) >> 15) & 1u) == level && ++t < 100000u) {
    }
    while (((RXA_REG(RXA_GPIOA + 0x10u) >> 15) & 1u) != level && ++t < 200000u) {
    }
    RXA_REG(RXA_SPI3 + RXA_SPI_I2SCFGR) |= 1u << 10;
}

/* the last 64 halfwords look like the codec's audio: each sample comes twice, a halfword apart
   (28 of 32 pairs at least, either way round), and few are the fill a slipped slot gives */
static int rxa_aligned(const rxa_state *s, unsigned wr) {
    unsigned eq0 = 0, eq1 = 0, fill = 0, m = s->ring - 1u;
    for (unsigned i = 0; i < 64u; i++) {
        unsigned a = (uint16_t)s->buf[(wr - 66u + i) & m], b = (uint16_t)s->buf[(wr - 65u + i) & m];
        if (a == b)
            *(i & 1u ? &eq1 : &eq0) += 1u;
        a = (a + 1u) & 0x3FFFu;          /* 0000 3fff 4000 7fff 8000 bfff c000 ffff -> 0 or 1 */
        fill += a < 2u;
    }
    return (eq0 >= 28u || eq1 >= 28u) && fill < 16u;
}

/* ring: n halfwords (a power of 2) in DMA-capable RAM (the module's scratch). Returns the tries
   it took to start in step with the codec's frames (1 to 16), or 0 if none looked right */
static unsigned rxa_start(const ortx_api_t *api, rxa_state *s, volatile int16_t *ring, unsigned n,
                          unsigned route) {
    s->ring = n, s->buf = ring, s->route = (uint8_t)route;
    s->r26 = api->c6000_read(0x26);
    s->mux = (RXA_REG(RXA_GPIOD + 0x14u) >> RXA_MUX_PIN) & 1u;      /* ODR */
    if (route) {
        api->c6000_write(0x26, 0xFD);
        RXA_REG(RXA_GPIOD + 0x18u) = 1u << RXA_MUX_PIN;
    }
    RXA_REG(RXA_RCC_AHB1ENR) |= 1u << 21;
    for (unsigned i = 0; i < 6; i++)
        s->dma[i] = RXA_REG(RXA_S0 + 4u * i);
    rxa_dma_off();
    RXA_REG(RXA_RCC_APB1ENR) |= 1u << 15;
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
    /* WS high first (Philips: the slave should be enabled with WS high), then low, in turn */
    for (unsigned k = 0; k < 16u; k++) {
        rxa_i2s_on((k & 1u) ^ 1u);
        api->sleep_ms(15);              /* 240 halfwords */
        if (rxa_aligned(s, (s->ring - RXA_REG(RXA_S0 + 4u)) & (s->ring - 1u)))
            return k + 1u;
    }
    return 0;
}

__attribute__((unused)) static unsigned rx_start(const ortx_api_t *api, rxa_state *s,
                                                 volatile int16_t *ring, unsigned n) {
    return rxa_start(api, s, ring, n, 1);
}

/* the capture alone: whatever the codec takes now (register 26 and PD9 left as they are, and
   left alone by rx_stop), e.g. while OpenRTX transmits */
__attribute__((unused)) static unsigned rx_start_as_is(const ortx_api_t *api, rxa_state *s,
                                                       volatile int16_t *ring, unsigned n) {
    return rxa_start(api, s, ring, n, 0);
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
    if (s->route) {
        api->c6000_write(0x26, s->r26);
        RXA_REG(RXA_GPIOD + 0x18u) = s->mux ? 1u << RXA_MUX_PIN : 1u << (RXA_MUX_PIN + 16);
    }
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
