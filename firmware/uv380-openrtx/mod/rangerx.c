/* rangerx: PHY R (docs/k6-range.md) received by the UV-380 with no hardware mod. The FM
   receiver's audio reaches the MCU through the HR_C6000's codec on I2S3 once register 26 is fd
   and RX_AUDIO_MUX (PD9) is high (the 11-13 14:52Z tone run, c6listen.c); SPI3 takes it as an I2S
   slave with 32-bit channels (P32), 8 kHz frames, each 16-bit sample in both channels a channel
   apart, so every other halfword is the audio. DMA1 stream 0 fills a ring in scratch RAM, the
   module follows it and hands the audio to c/common/range_rx.c: integrate and dump on the bit
   edges with a 16-phase clock, the sync, PHY G's Viterbi, the pager CRC.

   Lines:
     range start fs=8000 ring=1024 state=NNNN
     r 3 t=12345 c=-118 y=31 e=41 ok=1 s=2 r=-84
                    a packet: its number, ms at its end, the sync's correlation (128 from clean
                    soft values; its sign: the burst's way up), the mean |soft value| x 10 (40
                    clean), the line bits whose sign the decoded packet's own code disagrees with
                    (of 2064), CRC right, seq (meaningless unless ok=1), RSSI (dBm) at its end
     range end n=12 ok=11 ov=0 s=600
                    packets, CRC right, ring overruns (audio lost), seconds listened
   Listens RG_S seconds (Ctrl-C or PTT end it sooner); receive only: touches no RF register.
   Puts back register 26, PD9, the pins, SPI3 and the DMA stream as it found them.
   Returns the packets that checked, or -8 (scratch too small).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#define RR_HIST 32               /* 8 kHz: 2 bits are 13.3 samples */
#include "../../../c/common/range_rx.c"

#ifndef RG_S
#define RG_S 600u
#endif
#define RING 1024u               /* halfwords: 64 ms of audio */
#define FS 8000u

#define REG(a) (*(volatile uint32_t *)(a))
#define RCC_AHB1ENR 0x40023830u
#define RCC_APB1RSTR 0x40023820u
#define RCC_APB1ENR 0x40023840u
#define SPI3_BASE 0x40003C00u
#define SPI_CR2 0x04u
#define SPI_DR 0x0Cu
#define SPI_I2SCFGR 0x1Cu
#define SPI_I2SPR 0x20u
#define DMA1_LIFCR 0x40026008u
#define S0 0x40026010u            /* DMA1 stream 0: CR, NDTR, PAR, M0AR, M1AR, FCR */
#define GPIOA 0x40020000u
#define GPIOC 0x40020800u
#define GPIOD 0x40020C00u
#define MUX_PIN 9u

typedef struct {
    uint32_t moder, pupdr, afr;
} pinsave;

/* a pin to alternate function af (no pull), its old settings into *s */
static void pin_af(uint32_t port, unsigned pin, unsigned af, pinsave *s) {
    uint32_t afr = port + (pin < 8 ? 0x20u : 0x24u), sh = (pin & 7u) * 4u;
    s->moder = REG(port), s->pupdr = REG(port + 0x0Cu), s->afr = REG(afr);
    REG(port + 0x0Cu) = s->pupdr & ~(3u << (2 * pin));
    REG(afr) = (s->afr & ~(15u << sh)) | (af << sh);
    REG(port) = (s->moder & ~(3u << (2 * pin))) | (2u << (2 * pin));
}

static void pin_back(uint32_t port, unsigned pin, const pinsave *s) {
    REG(port) = s->moder;
    REG(port + 0x0Cu) = s->pupdr;
    REG(port + (pin < 8 ? 0x20u : 0x24u)) = s->afr;
}

int uv_main(const ortx_api_t *api) {
    char line[96];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    unsigned need = (unsigned)sizeof(rr_state) + RING * 2u;
    if (bytes < need) {
        api->fmt(line, sizeof line, "range scratch %lu < %u", (unsigned long)bytes, need);
        api->say(line);
        return -8;
    }
    rr_state *r = (rr_state *)mem;
    volatile int16_t *ring = (volatile int16_t *)(mem + sizeof(rr_state));
    rr_init(r, FS);
    api->fmt(line, sizeof line, "range start fs=%u ring=%u state=%u free=%lu", FS, RING,
             (unsigned)sizeof(rr_state), (unsigned long)(bytes - need));
    api->say(line);

    /* the receive route into the codec */
    uint8_t r26 = api->c6000_read(0x26);
    uint32_t mux = (REG(GPIOD + 0x14u) >> MUX_PIN) & 1u;   /* ODR */
    api->c6000_write(0x26, 0xFD);
    REG(GPIOD + 0x18u) = 1u << MUX_PIN;

    /* SPI3 as I2S slave receive, P32, into the ring by DMA1 stream 0 channel 0, circular */
    uint32_t dsave[6];
    REG(RCC_AHB1ENR) |= 1u << 21;
    for (unsigned i = 0; i < 6; i++)
        dsave[i] = REG(S0 + 4u * i);
    REG(S0) &= ~1u;
    for (unsigned t = 0; (REG(S0) & 1u) && t < 100000u; t++) {
    }
    REG(DMA1_LIFCR) = 0x3Du;
    REG(RCC_APB1ENR) |= 1u << 15;
    REG(RCC_APB1RSTR) |= 1u << 15;
    REG(RCC_APB1RSTR) &= ~(1u << 15);
    pinsave pws, pck, psd;
    pin_af(GPIOA, 15, 6, &pws);
    pin_af(GPIOC, 10, 6, &pck);
    pin_af(GPIOC, 12, 6, &psd);
    REG(S0 + 8u) = SPI3_BASE + SPI_DR;
    REG(S0 + 12u) = (uint32_t)(uintptr_t)ring;
    REG(S0 + 4u) = RING;
    REG(S0 + 20u) = 0;
    /* CHSEL 0, PL high, 16-bit memory and peripheral, MINC, CIRC, then EN */
    REG(S0) = (2u << 16) | (1u << 13) | (1u << 11) | (1u << 10) | (1u << 8);
    REG(S0) |= 1u;
    REG(SPI3_BASE + SPI_I2SPR) = 2u;
    REG(SPI3_BASE + SPI_I2SCFGR) = (1u << 11) | (1u << 8) | 1u;   /* I2SMOD, slave rx, CHLEN 32 */
    REG(SPI3_BASE + SPI_CR2) = 1u;                                /* RXDMAEN */
    REG(SPI3_BASE + SPI_I2SCFGR) |= 1u << 10;                     /* I2SE */

    unsigned rd = 0, npk = 0, nok = 0, ov = 0;
    int16_t x[64];
    while (api->ms() < RG_S * 1000u && !api->stop()) {
        unsigned wr = RING - REG(S0 + 4u), avail = (wr - rd + RING) % RING;
        if (avail > RING - 64u) {
            ov++;                       /* the ring went round: skip to what's fresh */
            rd = (wr + RING - 2u) & ~1u & (RING - 1u);
            continue;
        }
        if (avail < 2u) {
            api->sleep_ms(4);
            continue;
        }
        unsigned n = 0;
        while (avail >= 2u && n < 64u) {
            x[n++] = ring[(rd + 1u) % RING];    /* every other halfword: the audio at 8 kHz */
            rd = (rd + 2u) % RING;
            avail -= 2u;
        }
        unsigned off = 0, used;
        while (off < n) {
            if (rr_push(r, x + off, n - off, &used)) {
                npk++;
                nok += (unsigned)r->ok;
                api->fmt(line, sizeof line, "r %u t=%lu c=%d y=%d e=%d ok=%d s=%u r=%ld", npk,
                         (unsigned long)api->ms(), r->pcorr, r->ymean, r->errors, r->ok,
                         rr_seq(r->payload), (long)api->rssi());
                api->say(line);
            }
            off += used;
        }
    }

    REG(SPI3_BASE + SPI_I2SCFGR) &= ~(1u << 10);
    REG(SPI3_BASE + SPI_CR2) = 0;
    REG(SPI3_BASE + SPI_I2SCFGR) = 0;
    REG(S0) &= ~1u;
    for (unsigned t = 0; (REG(S0) & 1u) && t < 100000u; t++) {
    }
    REG(DMA1_LIFCR) = 0x3Du;
    for (unsigned i = 1; i < 6; i++)
        REG(S0 + 4u * i) = dsave[i];
    REG(S0) = dsave[0] & ~1u;
    pin_back(GPIOA, 15, &pws);
    pin_back(GPIOC, 10, &pck);
    pin_back(GPIOC, 12, &psd);
    REG(RCC_APB1RSTR) |= 1u << 15;
    REG(RCC_APB1RSTR) &= ~(1u << 15);
    REG(RCC_APB1ENR) &= ~(1u << 15);
    api->c6000_write(0x26, r26);
    REG(GPIOD + 0x18u) = mux ? 1u << MUX_PIN : 1u << (MUX_PIN + 16);
    api->fmt(line, sizeof line, "range end n=%u ok=%u ov=%u s=%lu", npk, nok, ov,
             (unsigned long)(api->ms() / 1000u));
    api->say(line);
    return (int)nok;
}
