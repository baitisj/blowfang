/* micrec_adc: the UV-380's microphone into the sotto 1200 encoder the way OpenRTX's M17 mode reads
   it (Jeff 11-14 03:27Z: "the m17 side records audio from the microphone"): ADC2 channel 3 = PA3
   (AIN_MIC) with the mic preamp powered by MIC_PWR (PA13), platform/drivers/audio/audio_MDx.cpp.
   micadc's hardware (TIM2 update at 8 kHz triggers ADC2, DMA2 stream 2 channel 1 into a circular
   ring in scratch, no interrupt), micrec's flow. micadc read nothing on PA3 on 11-13, but with the
   USB cable in, which switches the internal mic out; micrec's I2S3 capture carried the receiver.

     1. "micrec_adc ready", and the green LED blinks: unplug the cable (within MR_WAIT s). The
        unplug shows as api->stop() or as the USB core's suspend status (11-14 03:34Z stop()
        didn't turn); if neither does, it goes on after MR_WAIT anyway.
     2. LED off. Set 0: MR_SET ms with the preamp off (the control).
     3. LED green, steady: keep quiet. Set 1: MR_SETTLE ms with the preamp on, while its switch-on
        surge dies away (11-14 03:34Z: the mean fell from 553 to about 250 over 15 s, and a gain
        taken from the first 2 s came out at 2); it's the quiet baseline.
     4. LED orange (green and red): talk until it goes out (about 17 s), then plug the cable back
        in. Set 2: MR_SET ms of talking, the gain's measure: toward rms 3000, the peak kept under
        28000, 4 to MR_GMAX. Then MR_S s, the mean tracked out, into enc_begin/enc_push/enc_end:
        the recording becomes the message (PTT sends it, # plays it).
     5. The ADC mean every 100 ms through all of it ("env"): if PA3 carries a rectified level
        (OpenRTX's notes put a diode in its path) rather than audio, speech shows there.
     4. PA3, PA13, ADC2, TIM2 and the DMA stream put back. It waits up to MR_BACK s for any byte
        from USB (the cable back in and the host saying hello), then reports, so nothing is
        written while the cable is out.

   Lines (after the cable is back):
     mod say micrec set K rms=N pk=N n=N mean=N    K 0: preamp off, 1: on and quiet, 2: talking
                                                  (16-bit scale, mean out)
     mod say micrec use 2 gain=G
     mod say env IDX hhh hhh ...                   the ADC mean (0-fff) each 100 ms, 32 a line
     mod say micrec enc frames=N ov=N pushed=N ms=N  ov: ring overruns (the encoder fell behind)
     mod say m IDX hhhhhhhhhhhh ...                the message, as usbenc prints it
   Receive only: no RF register, no C6000 register, no TX. No loop ends on api->stop(), which only
   marks the cable coming out. The LED is PE0 (GREEN_LED), put back as found. Returns the
   superframes, or -1 (no memory for the encoder), -8 (scratch too small).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"

#define UV_MODULE_LEVEL 2

#ifndef MR_WAIT
#define MR_WAIT 120u
#endif
#ifndef MR_SET
#define MR_SET 2000u
#endif
#ifndef MR_S
#define MR_S 15u
#endif
#ifndef MR_BACK
#define MR_BACK 900u
#endif
#ifndef MR_SETTLE
#define MR_SETTLE 6000u
#endif
#ifndef MR_GMAX
#define MR_GMAX 128u
#endif
#define NENV 320u                  /* 100 ms means: 32 s */
#define FS 8000u
#define TIMCLK 84000000u           /* TIM2: APB1 x 2 at 168 MHz */
#define RING 2048u                 /* samples: 256 ms */

#define R(a) (*(volatile uint32_t *)(a))
#define RCC_APB1ENR 0x40023840u
#define RCC_APB2ENR 0x40023844u
#define RCC_AHB1ENR 0x40023830u
#define GPIOA 0x40020000u
#define GPIOE 0x40021000u
#define ADC2 0x40012100u
#define TIM2 0x40000000u
#define DMA2_LIFCR 0x40026408u
#define S2 0x40026440u             /* DMA2 stream 2: CR, NDTR, PAR, M0AR, M1AR, FCR */
#define OTG_DSTS 0x50000808u       /* USB OTG FS device status: bit 0 SUSPSTS */
#define MIC_PIN 13u
#define AIN_PIN 3u
#define LED_PIN 0u                 /* GREEN_LED, PE0 */
#define RED_PIN 1u                 /* RED_LED, PE1 */

typedef struct {
    uint32_t moder, odr, adc[5], tim[5], dma[6], apb1, apb2;
} ma_saved;

typedef struct {
    volatile uint16_t *ring;
    unsigned rd, ov;
    int32_t dcq;                   /* the mean, Q8 */
    uint32_t esum, ecnt;
    unsigned en;
    uint16_t *env;
} mr_t;

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

/* 0 off, 1 green, 3 orange (green and red) */
static void led(unsigned c) {
    R(GPIOE + 0x18u) = (c & 1u ? 1u << LED_PIN : 1u << (LED_PIN + 16)) |
                       (c & 2u ? 1u << RED_PIN : 1u << (RED_PIN + 16));
}

/* the cable out: stop() turned 1 (11-14 03:04Z), or the USB core sees the bus idle (suspend);
   11-14 03:34Z stop() never turned and the LED blinked its full MR_WAIT */
static int unplugged(const ortx_api_t *api) {
    return api->stop() || (R(OTG_DSTS) & 1u);
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
    R(TIM2 + 0x2Cu) = TIMCLK / FS - 1u;
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

/* up to n samples from the ring into x as 16-bit audio with the mean tracked out (a one-pole
   high-pass near 2 Hz); raw sums the 12-bit values; counts overruns */
static unsigned take(mr_t *m, int16_t *x, unsigned n, uint32_t *raw) {
    unsigned wr = wr_pos(), avail = (wr - m->rd) & (RING - 1u);
    if (avail > RING - 128u) {
        m->ov++;
        m->rd = (wr + RING - 1u) & (RING - 1u);
        return 0;
    }
    unsigned got = 0;
    while (got < n && avail) {
        int32_t v = m->ring[m->rd] & 0xFFF;
        m->rd = (m->rd + 1u) & (RING - 1u);
        avail--;
        *raw += (uint32_t)v;
        m->esum += (uint32_t)v;
        if (++m->ecnt == FS / 10u) {
            if (m->en < NENV)
                m->env[m->en++] = (uint16_t)(m->esum / m->ecnt);
            m->esum = m->ecnt = 0;
        }
        m->dcq += ((v << 8) - m->dcq) >> 9;
        int32_t y = (v * 256 - m->dcq) >> 4;    /* x16 to the 16-bit scale */
        x[got++] = (int16_t)(y > 32767 ? 32767 : y < -32767 ? -32767 : y);
    }
    return got;
}

/* ms of audio: rms, peak, count, ADC mean */
static void level(const ortx_api_t *api, mr_t *m, uint32_t ms, unsigned *rms, unsigned *pk,
                  unsigned *cnt, unsigned *mean) {
    int16_t x[64];
    uint32_t sq = 0, n = 0, raw = 0, end;
    unsigned p = 0;
    api->sleep_ms(200);                         /* the preamp settles */
    m->rd = wr_pos();
    m->dcq = (int32_t)(m->ring[(m->rd + RING - 1u) & (RING - 1u)] & 0xFFF) << 8;
    end = api->ms() + ms;
    while ((int32_t)(api->ms() - end) < 0) {
        unsigned g = take(m, x, 64u, &raw);
        if (!g) {
            api->sleep_ms(4);
            continue;
        }
        for (unsigned i = 0; i < g; i++) {
            int v = x[i];
            unsigned a = (unsigned)(v < 0 ? -v : v);
            p = a > p ? a : p;
            if (n < 65536u)
                sq += (uint32_t)(v * v) >> 16;
            n++;
        }
    }
    unsigned c = n < 65536u ? n : 65536u;
    *rms = c ? isqrt((sq / c) << 16) : 0u;
    *pk = p;
    *cnt = n;
    *mean = n ? raw / n : 0u;
}

int uv_main(const ortx_api_t *api) {
    char line[24 + 12 * 13];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < RING * 2u + NENV * 2u)
        return -8;
    mr_t m;
    api->memset(&m, 0, sizeof m);
    m.ring = (volatile uint16_t *)mem;
    m.env = (uint16_t *)(mem + RING * 2u);
    ma_saved s;
    start(&s, m.ring);
    mic(0);
    uint32_t e_moder = R(GPIOE), e_odr = R(GPIOE + 0x14u);
    R(GPIOE) = (e_moder & ~(15u << (2 * LED_PIN))) | (5u << (2 * LED_PIN));    /* PE0, PE1 out */
    api->fmt(line, sizeof line, "micrec_adc ready dsts=%lx: unplug the cable when the green LED blinks",
             (unsigned long)R(OTG_DSTS));
    api->say(line);
    /* blink until the cable comes out or MR_WAIT s */
    for (uint32_t t = api->ms(); api->ms() - t < 1000u * MR_WAIT && !unplugged(api);) {
        led(((api->ms() - t) / 250u) & 1u);
        api->sleep_ms(20);
    }
    led(0);
    unsigned rms[3], pk[3], cnt[3], mean[3];
    level(api, &m, MR_SET, &rms[0], &pk[0], &cnt[0], &mean[0]);     /* preamp off */
    led(1);                         /* quiet */
    mic(1);
    level(api, &m, MR_SETTLE, &rms[1], &pk[1], &cnt[1], &mean[1]);
    led(3);                         /* talk */
    level(api, &m, MR_SET, &rms[2], &pk[2], &cnt[2], &mean[2]);
    unsigned r = rms[2] ? rms[2] : 1u, gain = 3000u / r;
    if (pk[2] && gain * pk[2] > 28000u)
        gain = 28000u / pk[2];
    gain = gain < 4u ? 4u : gain > MR_GMAX ? MR_GMAX : gain;

    int frames = -1;
    unsigned pushed = 0;
    uint32_t t0 = api->ms(), raw = 0;
    if (api->enc_begin() == 0) {
        uint32_t end = api->ms() + 1000u * MR_S;
        m.rd = wr_pos();
        int16_t x[64];
        while ((int32_t)(api->ms() - end) < 0) {
            unsigned g = take(&m, x, 64u, &raw);
            if (!g) {
                api->sleep_ms(2);
                continue;
            }
            for (unsigned i = 0; i < g; i++) {
                int32_t v = (int32_t)x[i] * (int32_t)gain;
                x[i] = (int16_t)(v > 32767 ? 32767 : v < -32767 ? -32767 : v);
            }
            if (api->enc_push(x, g) < 0)
                break;
            pushed += g;
        }
        frames = api->enc_end();
    }
    uint32_t took = api->ms() - t0;
    stop(&s);                       /* PA3, PA13 (the preamp), ADC2, TIM2, DMA as found */
    led(0);
    R(GPIOE + 0x18u) = ((e_odr >> LED_PIN) & 1u ? 1u << LED_PIN : 1u << (LED_PIN + 16)) |
                       ((e_odr >> RED_PIN) & 1u ? 1u << RED_PIN : 1u << (RED_PIN + 16));
    R(GPIOE) = e_moder;             /* the LED as found: done, plug the cable back in */

    /* the cable back in: wait for the host's hello before writing anything */
    uint8_t b[16];
    for (uint32_t t = api->ms(); api->ms() - t < 1000u * MR_BACK;) {
        if (api->usb_read(b, sizeof b))
            break;
        api->sleep_ms(100);
    }
    for (unsigned k = 0; k < 3u; k++) {
        api->fmt(line, sizeof line, "micrec set %u rms=%u pk=%u n=%u mean=%u", k, rms[k], pk[k],
                 cnt[k], mean[k]);
        api->say(line);
    }
    api->fmt(line, sizeof line, "micrec use 2 gain=%u", gain);
    api->say(line);
    api->fmt(line, sizeof line, "micrec enc frames=%d ov=%u pushed=%u ms=%lu mean=%lu", frames,
             m.ov, pushed, (unsigned long)took, (unsigned long)(pushed ? raw / pushed : 0u));
    api->say(line);
    for (unsigned f = 0; f < m.en; f += 32u) {
        unsigned k = (unsigned)api->fmt(line, sizeof line, "env %u", f);
        for (unsigned j = f; j < m.en && j < f + 32u; j++) {
            line[k++] = ' ';
            for (int q = 8; q >= 0; q -= 4)
                line[k++] = hexd((m.env[j] >> q) & 15u);
        }
        line[k] = 0;
        api->say(line);
    }
    if (frames < 0) {
        api->say("micrec enc failed: no memory for the encoder");
        return -1;
    }
    if (frames == 0)
        return 0;                   /* msg() would be the built-in message */
    const uint8_t *msg;
    unsigned nf = api->msg(&msg);
    for (unsigned f = 0; msg && f < nf; f += 12u) {
        unsigned k = (unsigned)api->fmt(line, sizeof line, "m %u", f);
        for (unsigned j = f; j < nf && j < f + 12u; j++) {
            line[k++] = ' ';
            for (unsigned q = 0; q < 6u; q++) {
                line[k++] = hexd(msg[6u * j + q] >> 4);
                line[k++] = hexd(msg[6u * j + q] & 15u);
            }
        }
        line[k] = 0;
        api->say(line);
    }
    return frames;
}
