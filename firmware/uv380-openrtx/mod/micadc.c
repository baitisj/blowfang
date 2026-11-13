/* micadc: the UV-380's microphone straight into the MCU, no codec and no hardware mod. OpenRTX
   reads the MD-UV3x0 mic this way for M17 transmit (platform/drivers/audio/audio_MDx.cpp: ADC2
   channel 3 = PA3, AIN_MIC, with the mic preamp powered by MIC_PWR, PA13). The module drives the
   same hardware OpenRTX's ADC stream does (stm32_adc.cpp), while no stream runs: ADC2 triggered by
   TIM2's update at 8 kHz, DMA2 stream 2 channel 1 into a ring in scratch RAM, circular and with no
   interrupt, so OpenRTX's handler never sees it.

   Lines:
     mod say micadc ccr=NNNNNNNN cr2=NNNNNNNN tim2=PSC/ARR pa13=M/O       what it found
     mod say rate n=NNNNN ms=2000                 samples the DMA wrote in 2 s (16000 at 8 kHz)
     mod say set K mic=M                          before each part: 0 the preamp off, 1 on
     a SEQ hhhh ...                               64 samples a line, as rxrec: 12-bit ADC as
                                                  signed 16-bit ((v - 2048) * 16)
     mod say got K rms=N zc=N pk=N ov=N mean=N    level (16-bit scale, mean removed), zero
                                                  crossings a second, peak, overruns, ADC mean
     mod say micadc end lines=N
   MA_OFF s with the preamp off (a control), then MA_S s on. Touches no RF register and no
   C6000 register. Puts back PA3, PA13, ADC2, TIM2 and the DMA stream as it found them.
   tools/uv380_micprobe.py LOG --ref REF.wav scores each part; uv380_i2sprobe.py audio makes it a
   raw file. Returns the lines sent, or -8 (scratch too small).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#ifndef MA_S
#define MA_S 30u
#endif
#ifndef MA_OFF
#define MA_OFF 4u
#endif
#ifndef MA_FS
#define MA_FS 8000u
#endif
#define TIMCLK 84000000u           /* TIM2: APB1 x 2 at 168 MHz */
#define RING 1024u                 /* samples: 128 ms */
#define BLK 64u

#define R(a) (*(volatile uint32_t *)(a))
#define RCC_APB1ENR 0x40023840u
#define RCC_APB2ENR 0x40023844u
#define RCC_AHB1ENR 0x40023830u
#define GPIOA 0x40020000u
#define ADC2 0x40012100u
#define ADC_CCR 0x40012304u
#define TIM2 0x40000000u
#define DMA2_LIFCR 0x40026408u
#define S2 0x40026440u             /* DMA2 stream 2: CR, NDTR, PAR, M0AR, M1AR, FCR */
#define MIC_PIN 13u
#define AIN_PIN 3u

typedef struct {
    uint32_t moder, odr, adc[5], tim[5], dma[6], apb1, apb2;
} ma_saved;

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

static unsigned isqrt(uint32_t v) {
    uint32_t r = 0, b = 1u << 30;
    while (b > v)
        b >>= 2;
    while (b) {
        if (v >= r + b) {
            v -= r + b;
            r = (r >> 1) + b;
        } else {
            r >>= 1;
        }
        b >>= 2;
    }
    return (unsigned)r;
}

static void mic(unsigned on) {
    R(GPIOA) = (R(GPIOA) & ~(3u << (2 * MIC_PIN))) | (1u << (2 * MIC_PIN));
    R(GPIOA + 0x18u) = on ? 1u << MIC_PIN : 1u << (MIC_PIN + 16);
}

static void dma_off(void) {
    R(S2) &= ~1u;
    for (unsigned t = 0; (R(S2) & 1u) && t < 100000u; t++) {
    }
    R(DMA2_LIFCR) = 0x3Du << 16;
}

static const uint8_t ADC_OFF[] = {
    0x04,       /* CR1 */
    0x08,       /* CR2 */
    0x10,       /* SMPR2 */
    0x2C,       /* SQR1 */
    0x34,       /* SQR3 */
};

static const uint8_t TIM_OFF[] = {
    0x00,       /* CR1 */
    0x04,       /* CR2 */
    0x0C,       /* DIER */
    0x28,       /* PSC */
    0x2C,       /* ARR */
};

static void start(ma_saved *s, volatile uint16_t *ring) {
    s->moder = R(GPIOA), s->odr = R(GPIOA + 0x14u);
    s->apb1 = R(RCC_APB1ENR), s->apb2 = R(RCC_APB2ENR);
    R(RCC_APB1ENR) |= 1u;                       /* TIM2 */
    R(RCC_APB2ENR) |= 1u << 9;                  /* ADC2 */
    R(RCC_AHB1ENR) |= 1u << 22;                 /* DMA2 */
    for (unsigned i = 0; i < sizeof ADC_OFF; i++)
        s->adc[i] = R(ADC2 + ADC_OFF[i]);
    for (unsigned i = 0; i < sizeof TIM_OFF; i++)
        s->tim[i] = R(TIM2 + TIM_OFF[i]);
    for (unsigned i = 0; i < 6u; i++)
        s->dma[i] = R(S2 + 4u * i);
    R(GPIOA) |= 3u << (2 * AIN_PIN);            /* PA3 analog */

    R(TIM2) = 0;
    R(TIM2 + 0x0Cu) = 0;
    R(ADC2 + 0x08u) = 0;                        /* ADON off while it's set up */
    dma_off();
    R(S2 + 8u) = ADC2 + 0x4Cu;
    R(S2 + 12u) = (uint32_t)(uintptr_t)ring;
    R(S2 + 4u) = RING;
    R(S2 + 20u) = 0;
    /* CHSEL 1, PL high, 16-bit memory and peripheral, MINC, CIRC, no interrupts, then EN */
    R(S2) = (1u << 25) | (2u << 16) | (1u << 13) | (1u << 11) | (1u << 10) | (1u << 8);
    R(S2) |= 1u;

    R(ADC2 + 0x10u) = (s->adc[2] & ~(7u << (3 * AIN_PIN))) | (4u << (3 * AIN_PIN));  /* 84 cyc */
    R(ADC2 + 0x2Cu) = 0;                        /* one conversion */
    R(ADC2 + 0x34u) = AIN_PIN;
    R(ADC2 + 0x04u) = 0;                        /* 12 bits, no scan, no discontinuous */
    R(ADC2 + 0x00u) = 0;                        /* clear OVR, EOC */
    /* rising edge of TIM2_TRGO, DMA with continuous requests, ADON */
    R(ADC2 + 0x08u) = (1u << 28) | (6u << 24) | (1u << 9) | (1u << 8) | 1u;

    R(TIM2 + 0x28u) = 0;
    R(TIM2 + 0x2Cu) = TIMCLK / MA_FS - 1u;
    R(TIM2 + 0x04u) = 2u << 4;                  /* MMS: update as TRGO */
    R(TIM2 + 0x24u) = 0;
    R(TIM2 + 0x14u) = 1u;                       /* load PSC and ARR */
    R(TIM2) = 1u;                               /* CEN */
}

static void stop(const ma_saved *s) {
    R(TIM2) = 0;
    R(ADC2 + 0x08u) = 0;
    dma_off();
    for (unsigned i = 1; i < 6u; i++)
        R(S2 + 4u * i) = s->dma[i];
    R(S2) = s->dma[0] & ~1u;
    R(ADC2 + 0x00u) = 0;
    for (unsigned i = 0; i < sizeof ADC_OFF; i++)
        R(ADC2 + ADC_OFF[i]) = s->adc[i];
    for (unsigned i = 1; i < sizeof TIM_OFF; i++)
        R(TIM2 + TIM_OFF[i]) = s->tim[i];
    R(TIM2 + 0x14u) = 1u;
    R(TIM2) = s->tim[0];
    R(GPIOA + 0x18u) = (s->odr >> MIC_PIN) & 1u ? 1u << MIC_PIN : 1u << (MIC_PIN + 16);
    R(GPIOA) = s->moder;
    R(RCC_APB2ENR) = s->apb2;
    R(RCC_APB1ENR) = s->apb1;
}

static unsigned wr_pos(void) {
    return (RING - R(S2 + 4u)) & (RING - 1u);
}

int uv_main(const ortx_api_t *api) {
    char line[24 + BLK * 5u];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < RING * 2u)
        return -8;
    volatile uint16_t *ring = (volatile uint16_t *)mem;
    ma_saved s;
    start(&s, ring);
    api->fmt(line, sizeof line, "micadc ccr=%08lx cr2=%08lx tim2=%lu/%lu pa13=%lu/%lu",
             (unsigned long)R(ADC_CCR), (unsigned long)s.adc[1], (unsigned long)s.tim[3],
             (unsigned long)s.tim[4], (unsigned long)((s.moder >> (2 * MIC_PIN)) & 3u),
             (unsigned long)((s.odr >> MIC_PIN) & 1u));
    api->say(line);

    /* the rate: samples written over 2 s, read every 10 ms (the ring is 128 ms) */
    unsigned last = wr_pos();
    uint32_t n = 0, t0 = api->ms();
    while (api->ms() - t0 < 2000u && !api->stop()) {
        api->sleep_ms(10);
        unsigned w = wr_pos();
        n += (w - last) & (RING - 1u);
        last = w;
    }
    api->fmt(line, sizeof line, "rate n=%lu ms=%lu sr=%02lx", (unsigned long)n,
             (unsigned long)(api->ms() - t0), (unsigned long)R(ADC2));
    api->say(line);

    uint32_t seq = 0;
    for (unsigned k = 0; k < 2u && !api->stop(); k++) {
        mic(k);
        api->fmt(line, sizeof line, "set %u mic=%u", k, k);
        api->say(line);
        api->sleep_ms(200);                     /* the preamp settles */
        unsigned rd = wr_pos(), ov = 0, zc = 0, pk = 0, got = 0;
        uint32_t sum = 0, sq = 0, end = api->ms() + 1000u * (k ? MA_S : MA_OFF);
        int dc = 2048, prev = 0;
        while ((int32_t)(api->ms() - end) < 0 && !api->stop()) {
            unsigned wr = wr_pos(), avail = (wr - rd) & (RING - 1u);
            if (avail > RING - 128u) {
                ov++;
                rd = (wr + RING - 1u) & (RING - 1u);
                seq++;
                continue;
            }
            if (avail < BLK) {
                api->sleep_ms(2);
                continue;
            }
            unsigned m = (unsigned)api->fmt(line, 24, "a %lu", (unsigned long)seq++);
            for (unsigned i = 0; i < BLK; i++) {
                int v = ring[rd] & 0xFFF;
                rd = (rd + 1u) & (RING - 1u);
                sum += (uint32_t)v;
                int x = (v - dc) * 16;           /* dc: the mean so far */
                x = x > 32767 ? 32767 : x < -32767 ? -32767 : x;
                unsigned a = (unsigned)(x < 0 ? -x : x);
                if (a > pk)
                    pk = a;
                if ((x < 0) != (prev < 0))
                    zc++;
                prev = x;
                if (got < 65536u)
                    sq += (uint32_t)(x * x) >> 16;
                got++;
                int16_t h = (int16_t)((v - 2048) * 16);
                line[m++] = ' ';
                for (int b = 12; b >= 0; b -= 4)
                    line[m++] = hexd(((uint16_t)h >> b) & 15u);
            }
            dc = (int)(sum / got);
            line[m++] = '\r';
            line[m++] = '\n';
            api->usb_write(line, m);
        }
        unsigned c = got < 65536u ? got : 65536u;
        api->fmt(line, sizeof line, "got %u rms=%u zc=%lu pk=%u ov=%u mean=%lu", k,
                 c ? isqrt((sq / c) << 16) : 0u,
                 got ? (unsigned long)((uint32_t)zc * MA_FS / got) : 0ul,
                 pk, ov, got ? (unsigned long)(sum / got) : 0ul);
        api->say(line);
    }
    stop(&s);
    api->fmt(line, sizeof line, "micadc end lines=%lu", (unsigned long)seq);
    api->say(line);
    return (int)seq;
}
