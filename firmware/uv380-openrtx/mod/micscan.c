/* micscan: which route, if any, carries the UV-380's microphone, scored against a known sound
   instead of by ear. The IC-7100 sends a 1000 Hz tone keyed on and off by a 31-chip m-sequence
   (100 ms chips, 3.1 s period) on 440.850; the UV-380's receiver plays it on its own speaker,
   and the module listens on both mic routes at once:
     c  the C6000 codec input on I2S3 (rxaudio.h, the capture alone): the 1000 Hz level each 20 ms
     a  PA3 (AIN_MIC, ADC2 channel 3, micadc's TIM2/DMA2 capture): the 1000 Hz level and the mean
        each 20 ms (11-14 03:42Z the pin was half-wave, so its mean follows a tone)
   and correlates each with the m-sequence over every circular lag. Steady noise, the preamp's
   drift and anything not keyed by the pattern score near 0; the pattern scores up to 1000. The
   best of 155 lags of pure noise reads about 170 (host model, white noise): over 300 is real.
   The receiver also reaches the codec input electrically (11-14 03:12Z), so the codec input
   scores with the speaker off too: the mic's share is what the speaker and the preamp add.

   Windows (6.2 s each: two periods), in this order so the preamp switches on once:
     0  speaker off, preamp off, 26=7d          the receiver's electrical leak alone
     1  speaker on,  preamp off, 26=7d          + the speaker, no mic
     (preamp on, MS_SETTLE ms for its switch-on surge)
     2  speaker off, preamp on,  26=7d
     3  speaker on,  preamp on,  26=7d          the mic, if it hears the speaker
     4  speaker on,  preamp on,  26=bd
     5  speaker on,  preamp on,  OpenGD77's DMR codec set (E0 8b E2 06 E4 4a)
     6  speaker on,  preamp on,  E0 c1 (OpenRTX's FM mic)
     7  speaker on,  preamp on,  26 as found
     8  speaker on,  preamp on,  26=7d, PD9 the other way (it may switch the speaker's source)
     9  speaker off, preamp on,  26=7d, PD9 the other way
   The speaker plays at the volume knob's level (micspk's amp and mute sequence).

   Flow: loaded with the USB cable in (it switches the mic out). "micscan ready", green LED
   blinks: unplug. The unplug shows as api->stop() or the USB core's suspend status. Unplugging
   may look like a PTT press, which in PAGER mode sends the stored message: the IC-7100 listens
   for MS_PRE ms first (the runner starts the pattern 25 s after the unplug), then the windows run
   with the LED steady. LED off: done, plug back in; it reports after the host's hello:
     mod say micscan regs 26=.. e0=.. e2=.. e4=.. pd9=. align=N
     mod say scan K spk=S mic=M set=X | c rms=N r=NNN L=NNN A=N | a mean=N rms=N r=NNN L=NNN
           A=N rm=NNN Lm=NNN Am=N ov=N
       r: the best correlation (x1000), L its lag (20 ms steps, 0-154), A the pattern's level in
       it (half the on-off difference); rm/Lm/Am the same for PA3's mean
     mod say micscan end
   Receive only: no RF register, no TX. Puts back 26, E0, E2, E4, PD9, PA3, PA13, PB8, PB9, PE0,
   ADC2, TIM2 and both DMA streams. Returns 0, or -8 (scratch too small), -1000 (no C6000).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "rxaudio.h"

#define UV_MODULE_LEVEL 1

#ifndef MS_WAIT
#define MS_WAIT 120u                /* s: the blink, waiting for the unplug */
#endif
#ifndef MS_PRE
#define MS_PRE 34500u               /* ms: the IC-7100 listens 25 s, sends its ident, then the pattern */
#endif
#ifndef MS_SETTLE
#define MS_SETTLE 4000u
#endif
#ifndef MS_BACK
#define MS_BACK 3600u               /* s: 11-14 06:32Z the report was lost after 900 s */
#endif
#define NWIN 10u
#define SUB 160u                    /* samples: 20 ms */
#define NLAG 155u                   /* 31 chips x 5 */
#define NT (2u * NLAG)              /* sub-chips a window: two periods */
#define CRING 2048u                 /* I2S3 halfwords: 1024 samples, 128 ms */
#define ARING 1024u                 /* ADC samples: 128 ms */
#define GC 1.41421356f              /* Goertzel 2 cos(2 pi 1000 / 8000) */

#define R(a) (*(volatile uint32_t *)(a))
#define RCC_APB1ENR 0x40023840u
#define RCC_APB2ENR 0x40023844u
#define RCC_AHB1ENR 0x40023830u
#define GPIOA 0x40020000u
#define GPIOB 0x40020400u
#define GPIOE 0x40021000u
#define ADC2 0x40012100u
#define TIM2 0x40000000u
#define DMA2_LIFCR 0x40026408u
#define S2 0x40026440u
#define OTG_DSTS 0x50000808u
#define MIC_PIN 13u
#define AIN_PIN 3u
#define LED_PIN 0u

/* one input's correlation against the pattern */
typedef struct {
    float s[NLAG], sum, sq;
} corr_t;

/* one input's running 20 ms block */
typedef struct {
    float g1, g2;
    float sum, sq;
    uint32_t n;
} blk_t;

typedef struct {
    corr_t cg, ag, am;              /* codec 1 kHz, PA3 1 kHz, PA3 mean */
    blk_t cb, ab;
    float crms, arms, amean;        /* window sums of the block values */
    unsigned tc, ta, ov;            /* blocks done: codec, PA3 */
    int8_t pat[NLAG];               /* the m-sequence, 5 x upsampled: +1 tone on, -1 off */
} win_t;

typedef struct {
    uint32_t moder, odr, adc[5], tim[5], dma[6], apb1, apb2;
} adc_saved;

static const uint8_t ADC_OFF[] = {0x04, 0x08, 0x10, 0x2C, 0x34};
static const uint8_t TIM_OFF[] = {0x00, 0x04, 0x0C, 0x28, 0x2C};

static char hexd(unsigned v) {
    return (char)(v < 10u ? '0' + v : 'a' + v - 10u);
}

static float fsqrt(float v) {
    if (v <= 0.0f)
        return 0.0f;
    float r = v > 1.0f ? v : 1.0f;
    for (int i = 0; i < 40; i++)
        r = 0.5f * (r + v / r);
    return r;
}

/* the m-sequence, 5 x upsampled, +1 tone on, -1 off: the runner's generator, step for step */
static void make_pat(int8_t *pat) {
    unsigned s = 1u;
    for (unsigned c = 0; c < 31u; c++) {
        unsigned out = s & 1u, fb = (s ^ (s >> 2)) & 1u;
        s = (s >> 1) | (fb << 4);
        for (unsigned k = 0; k < 5u; k++)
            pat[5u * c + k] = out ? 1 : -1;
    }
}

static void mic(unsigned on) {
    R(GPIOA) = (R(GPIOA) & ~(3u << (2 * MIC_PIN))) | (1u << (2 * MIC_PIN));
    R(GPIOA + 0x18u) = on ? 1u << MIC_PIN : 1u << (MIC_PIN + 16);
}

static void spk(const ortx_api_t *api, unsigned on) {
    if (on) {
        R(GPIOB + 0x18u) = 1u << 9;             /* amp on */
        api->sleep_ms(10);
        R(GPIOB + 0x18u) = 1u << (8 + 16);      /* unmute */
    } else {
        R(GPIOB + 0x18u) = 1u << 8;             /* mute */
        R(GPIOB + 0x18u) = 1u << (9 + 16);      /* amp off */
    }
}

static void led(unsigned on) {
    R(GPIOE + 0x18u) = on ? 1u << LED_PIN : 1u << (LED_PIN + 16);
}

static int unplugged(const ortx_api_t *api) {
    return api->stop() || (R(OTG_DSTS) & 1u);
}

static void dma2_off(void) {
    R(S2) &= ~1u;
    for (unsigned t = 0; (R(S2) & 1u) && t < 100000u; t++) {
    }
    R(DMA2_LIFCR) = 0x3Du << 16;
}

static void adc_start(adc_saved *s, volatile uint16_t *ring) {
    s->moder = R(GPIOA), s->odr = R(GPIOA + 0x14u);
    s->apb1 = R(RCC_APB1ENR), s->apb2 = R(RCC_APB2ENR);
    R(RCC_APB1ENR) |= 1u;
    R(RCC_APB2ENR) |= 1u << 9;
    R(RCC_AHB1ENR) |= 1u << 22;
    for (unsigned i = 0; i < sizeof ADC_OFF; i++)
        s->adc[i] = R(ADC2 + ADC_OFF[i]);
    for (unsigned i = 0; i < sizeof TIM_OFF; i++)
        s->tim[i] = R(TIM2 + TIM_OFF[i]);
    for (unsigned i = 0; i < 6u; i++)
        s->dma[i] = R(S2 + 4u * i);
    R(GPIOA) |= 3u << (2 * AIN_PIN);
    R(TIM2) = 0;
    R(TIM2 + 0x0Cu) = 0;
    R(ADC2 + 0x08u) = 0;
    dma2_off();
    R(S2 + 8u) = ADC2 + 0x4Cu;
    R(S2 + 12u) = (uint32_t)(uintptr_t)ring;
    R(S2 + 4u) = ARING;
    R(S2 + 20u) = 0;
    R(S2) = (1u << 25) | (2u << 16) | (1u << 13) | (1u << 11) | (1u << 10) | (1u << 8);
    R(S2) |= 1u;
    R(ADC2 + 0x10u) = (s->adc[2] & ~(7u << (3 * AIN_PIN))) | (4u << (3 * AIN_PIN));
    R(ADC2 + 0x2Cu) = 0;
    R(ADC2 + 0x34u) = AIN_PIN;
    R(ADC2 + 0x04u) = 0;
    R(ADC2 + 0x00u) = 0;
    R(ADC2 + 0x08u) = (1u << 28) | (6u << 24) | (1u << 9) | (1u << 8) | 1u;
    R(TIM2 + 0x28u) = 0;
    R(TIM2 + 0x2Cu) = 84000000u / 8000u - 1u;
    R(TIM2 + 0x04u) = 2u << 4;
    R(TIM2 + 0x24u) = 0;
    R(TIM2 + 0x14u) = 1u;
    R(TIM2) = 1u;
}

static void adc_stop(const adc_saved *s) {
    R(TIM2) = 0;
    R(ADC2 + 0x08u) = 0;
    dma2_off();
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

static unsigned adc_wr(void) {
    return (ARING - R(S2 + 4u)) & (ARING - 1u);
}

static void corr_add(corr_t *c, const int8_t *pat, float x, unsigned t) {
    unsigned j = t % NLAG;
    for (unsigned l = 0; l < NLAG; l++) {
        c->s[l] += pat[j] > 0 ? x : -x;
        if (++j == NLAG)
            j = 0;
    }
    c->sum += x;
    c->sq += x * x;
}

/* best lag: r x1000, lag, level A */
static void corr_best(const corr_t *c, unsigned n, int *r, unsigned *lag, int *amp) {
    /* over whole periods the pattern sums to +1 a chip (16 on, 15 off): 5 a period of blocks */
    float mean = c->sum / (float)n, var = c->sq - c->sum * mean;
    float bias = mean * 5.0f * (float)(n / NLAG);
    float best = -1e30f;
    unsigned bl = 0;
    for (unsigned l = 0; l < NLAG; l++) {
        float v = c->s[l] - bias;
        if (v > best)
            best = v, bl = l;
    }
    float den = fsqrt((float)n * (var > 0.0f ? var : 0.0f));
    *r = den > 0.0f ? (int)(1000.0f * best / den) : 0;
    *lag = bl;
    *amp = (int)(best / (float)n);
}

/* one sample into a block; at SUB samples, the block's 1 kHz level (and mean) out */
static int blk_add(blk_t *b, int32_t x, float *g, float *mean, float *rms) {
    float s0 = (float)x + GC * b->g1 - b->g2;
    b->g2 = b->g1, b->g1 = s0;
    b->sum += (float)x;
    b->sq += (float)x * (float)x;
    if (++b->n < SUB)
        return 0;
    float p = b->g1 * b->g1 + b->g2 * b->g2 - GC * b->g1 * b->g2;
    *g = 2.0f * fsqrt(p) / (float)SUB;
    *mean = b->sum / (float)SUB;
    float v = b->sq / (float)SUB - *mean * *mean;
    *rms = fsqrt(v);
    b->g1 = b->g2 = 0.0f, b->sum = 0.0f, b->sq = 0.0f, b->n = 0;
    return 1;
}

/* the C6000 settings: 0 7d, 1 bd, 2 DMR, 3 E0 c1, 4 26 as found */
static void codec(const ortx_api_t *api, unsigned k, const uint8_t *f) {
    api->c6000_write(0xE0, k == 2u ? 0x8B : k == 3u ? 0xC1 : f[1]);
    api->c6000_write(0xE2, k == 2u ? 0x06 : f[2]);
    api->c6000_write(0xE4, k == 2u ? 0x4A : f[3]);
    api->c6000_write(0x26, k == 1u ? 0xBD : k == 4u ? f[0] : 0x7D);
}

static const uint8_t WSPK[NWIN] = {0, 1, 0, 1, 1, 1, 1, 1, 1, 0};
static const uint8_t WMIC[NWIN] = {0, 0, 1, 1, 1, 1, 1, 1, 1, 1};
static const uint8_t WSET[NWIN] = {0, 0, 0, 0, 1, 2, 3, 4, 0, 0};
static const uint8_t WPD9[NWIN] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1};     /* 1: the other way */

typedef struct {
    int cr, ar, amr, ca, aa, ama;
    unsigned cl, al, aml, crms, arms, amean, ov;
} res_t;

int uv_main(const ortx_api_t *api) {
    char line[200];
    uint32_t bytes;
    uint8_t *mem = (uint8_t *)api->scratch(&bytes);
    if (bytes < CRING * 2u + ARING * 2u + sizeof(win_t) + NWIN * sizeof(res_t))
        return -8;
    volatile int16_t *cring = (volatile int16_t *)mem;
    volatile uint16_t *aring = (volatile uint16_t *)(mem + CRING * 2u);
    win_t *w = (win_t *)(mem + CRING * 2u + ARING * 2u);
    res_t *res = (res_t *)(w + 1);
    if (api->c6000_read(0x00) == 0x00) {
        api->say("micscan: the C6000 doesn't answer; power-cycle first");
        return -1000;
    }
    uint8_t f[4] = {api->c6000_read(0x26), api->c6000_read(0xE0), api->c6000_read(0xE2),
                    api->c6000_read(0xE4)};
    uint32_t pd9 = (R(RXA_GPIOD + 0x14u) >> RXA_MUX_PIN) & 1u;
    uint32_t pb = R(GPIOB + 0x14u), pb_moder = R(GPIOB);
    uint32_t e_moder = R(GPIOE), e_odr = R(GPIOE + 0x14u);
    rxa_state rs;
    unsigned align = rx_start_as_is(api, &rs, cring, CRING);
    adc_saved as;
    adc_start(&as, aring);
    mic(0);
    R(GPIOB) = (pb_moder & ~(15u << 16)) | (5u << 16);     /* PB8, PB9 out */
    spk(api, 0);
    R(GPIOE) = (e_moder & ~(3u << (2 * LED_PIN))) | (1u << (2 * LED_PIN));
    api->fmt(line, sizeof line,
             "micscan ready 26=%02x e0=%02x e2=%02x e4=%02x pd9=%lu align=%u: unplug the cable "
             "when the green LED blinks", f[0], f[1], f[2], f[3], (unsigned long)pd9, align);
    api->say(line);

    int out = 0;
    for (uint32_t t = api->ms(); api->ms() - t < 1000u * MS_WAIT && !(out = unplugged(api));) {
        led(((api->ms() - t) / 250u) & 1u);
        api->sleep_ms(20);
    }
    led(1);                         /* steady: leave it alone */
    uint32_t t_out = api->ms();
    while (api->ms() - t_out < MS_PRE)
        api->sleep_ms(100);

    for (unsigned k = 0; k < NWIN; k++) {
        if (k == 2u) {
            mic(1);
            api->sleep_ms(MS_SETTLE);
        }
        codec(api, WSET[k], f);
        R(RXA_GPIOD + 0x18u) = (pd9 ^ WPD9[k]) ? 1u << RXA_MUX_PIN : 1u << (RXA_MUX_PIN + 16);
        spk(api, WSPK[k]);
        api->sleep_ms(300);
        api->memset(w, 0, sizeof *w);
        make_pat(w->pat);
        unsigned crd = rx_wr(&rs) & ~1u, ard = adc_wr();
        while (w->tc < NT || w->ta < NT) {
            unsigned cw = rx_wr(&rs), aw = adc_wr(), did = 0;
            unsigned cav = (cw - crd) & (CRING - 1u), aav = (aw - ard) & (ARING - 1u);
            if (cav > CRING - 256u) {
                w->ov++;
                crd = (cw + CRING - 2u) & ~1u & (CRING - 1u);
                cav = 0;
            }
            if (aav > ARING - 128u) {
                w->ov++;
                ard = (aw + ARING - 1u) & (ARING - 1u);
                aav = 0;
            }
            while (cav >= 2u && w->tc < NT) {
                float g, m, r;
                int32_t x = cring[(crd + 1u) & (CRING - 1u)];
                crd = (crd + 2u) & (CRING - 1u);
                cav -= 2u, did = 1;
                if (blk_add(&w->cb, x, &g, &m, &r)) {
                    corr_add(&w->cg, w->pat, g, w->tc++);
                    w->crms += r;
                }
            }
            while (aav && w->ta < NT) {
                float g, m, r;
                int32_t x = aring[ard] & 0xFFF;
                ard = (ard + 1u) & (ARING - 1u);
                aav--, did = 1;
                if (blk_add(&w->ab, x, &g, &m, &r)) {
                    corr_add(&w->ag, w->pat, g, w->ta);
                    corr_add(&w->am, w->pat, m, w->ta++);
                    w->arms += r, w->amean += m;
                }
            }
            if (!did)
                api->sleep_ms(4);
        }
        res_t *q = &res[k];
        corr_best(&w->cg, NT, &q->cr, &q->cl, &q->ca);
        corr_best(&w->ag, NT, &q->ar, &q->al, &q->aa);
        corr_best(&w->am, NT, &q->amr, &q->aml, &q->ama);
        q->crms = (unsigned)(w->crms / (float)NT);
        q->arms = (unsigned)(w->arms / (float)NT);
        q->amean = (unsigned)(w->amean / (float)NT);
        q->ov = w->ov;
    }
    uint8_t g[4] = {api->c6000_read(0x26), api->c6000_read(0xE0), api->c6000_read(0xE2),
                    api->c6000_read(0xE4)};

    spk(api, 0);
    adc_stop(&as);                  /* PA3, PA13 (the preamp) as found */
    rx_stop(api, &rs);
    api->c6000_write(0x26, f[0]);
    api->c6000_write(0xE0, f[1]);
    api->c6000_write(0xE2, f[2]);
    api->c6000_write(0xE4, f[3]);
    R(RXA_GPIOD + 0x18u) = pd9 ? 1u << RXA_MUX_PIN : 1u << (RXA_MUX_PIN + 16);
    R(GPIOB + 0x18u) = ((pb >> 8) & 1u ? 1u << 8 : 1u << 24) |
                       ((pb >> 9) & 1u ? 1u << 9 : 1u << 25);
    R(GPIOB) = pb_moder;
    R(GPIOE + 0x18u) = (e_odr >> LED_PIN) & 1u ? 1u << LED_PIN : 1u << (LED_PIN + 16);
    R(GPIOE) = e_moder;             /* the LED as found: plug the cable back in */

    uint8_t b[16];
    for (uint32_t t = api->ms(); api->ms() - t < 1000u * MS_BACK;) {
        if (api->usb_read(b, sizeof b))
            break;
        api->sleep_ms(100);
    }
    api->fmt(line, sizeof line, "micscan unplug=%d regs after 26=%02x e0=%02x e2=%02x e4=%02x",
             out, g[0], g[1], g[2], g[3]);
    api->say(line);
    for (unsigned k = 0; k < NWIN; k++) {
        const res_t *q = &res[k];
        static const char SETN[] = "7bDc=";             /* 7d, bd, DMR, c1, as found */
        api->fmt(line, sizeof line,
                 "scan %u spk=%u mic=%u set=%c pd9=%u | c rms=%u r=%d L=%u A=%d | a mean=%u rms=%u "
                 "r=%d L=%u A=%d rm=%d Lm=%u Am=%d ov=%u", k, WSPK[k], WMIC[k], SETN[WSET[k]],
                 (unsigned)(pd9 ^ WPD9[k]), q->crms, q->cr, q->cl, q->ca, q->amean, q->arms, q->ar,
                 q->al, q->aa, q->amr, q->aml, q->ama, q->ov);
        api->say(line);
    }
    (void)hexd;
    api->say("micscan end");
    return 0;
}
