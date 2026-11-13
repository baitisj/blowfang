/* UV-380 I2S probe (i2sprobe.h). SPDX-License-Identifier: MIT */
#include "i2sprobe.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

/* ---- the analysis (host and radio) ---- */

#define AN_MAX 1024u             /* samples analysed at most (the first ones) */
static float an[AN_MAX];

void i2sp_analyse(const int16_t *x, unsigned n, unsigned stride, unsigned fs, i2sp_stats *s)
{
    unsigned i, j, m, k, best = 0;
    int64_t sum = 0;
    float e = 0.0f, mean, total = 0.0f, band[I2SP_BINS + 1];
    memset(s, 0, sizeof *s);
    s->n = n;
    if (n == 0 || stride == 0)
        return;
    for (i = 0; i < n; i++) {
        int v = x[i * stride];
        sum += v;
        s->nz += v != 0;
        s->changes += i > 0 && v != x[(i - 1) * stride];
    }
    mean = (float)sum / (float)n;
    s->mean = (int32_t)lroundf(mean);
    for (i = 0; i < n; i++) {
        float d = (float)x[i * stride] - mean;
        e += d * d;
    }
    s->rms = (uint32_t)lroundf(sqrtf(e / (float)n));
    m = n < AN_MAX ? n : AN_MAX;
    if (e <= 0.0f || m < 64)
        return;
    /* the power at every frequency fs j / m (Goertzel, Hann window), summed into 31 bands
       around fs k / 64: the strongest band and its share of all of it */
    for (i = 0; i < m; i++)
        an[i] = ((float)x[i * stride] - mean) * (0.5f - 0.5f * cosf(6.28318531f * (float)i / (float)(m - 1)));
    memset(band, 0, sizeof band);
    for (j = 1; j < m / 2; j++) {
        float c = 2.0f * cosf(6.28318531f * (float)j / (float)m), s1 = 0.0f, s2 = 0.0f, p;
        for (i = 0; i < m; i++) {
            float s0 = an[i] + c * s1 - s2;
            s2 = s1;
            s1 = s0;
        }
        p = s1 * s1 + s2 * s2 - c * s1 * s2;
        total += p;
        k = (64u * j + m / 2) / m;           /* the nearest fs k / 64 */
        if (k >= 1 && k <= I2SP_BINS)
            band[k] += p;
    }
    for (k = 1; k <= I2SP_BINS; k++)
        if (band[k] > band[best] || best == 0)
            best = k;
    s->peak_hz = fs * best / 64u;
    s->peak_pm = total > 0.0f ? (uint32_t)lroundf(1000.0f * band[best] / total) : 0;
}

unsigned i2sp_format(char *buf, unsigned size, const char *tag, const i2sp_stats *s)
{
    unsigned len = (unsigned)strlen(buf);
    if (len < size)
        snprintf(buf + len, size - len, " %s:nz=%lu ch=%lu m=%ld r=%lu pk=%lu/%lu", tag,
                 (unsigned long)s->nz, (unsigned long)s->changes, (long)s->mean,
                 (unsigned long)s->rms, (unsigned long)s->peak_hz, (unsigned long)s->peak_pm);
    return (unsigned)strlen(buf);
}

#if defined(PLATFORM_MDUV3x0)
/* ---- the radio ---- */
#include "stm32f4xx.h"

#define LINE 320
#define NCAP 2048u              /* halfwords a capture, each of SD and ext SD */
#define CENSUS_MS 5u
#define I2SCLK 128000000u       /* PLLI2S: 1 MHz in (PLL_M = HSE / 1 MHz), N 256, R 2 */

typedef struct {
    GPIO_TypeDef *port;
    uint8_t pin;
    char name[5];
} pin_t;

static const pin_t PINS[] = {
    {GPIOA, 15, "PA15"},        /* SPI3 WS */
    {GPIOC, 10, "PC10"},        /* SPI3 CK */
    {GPIOC, 11, "PC11"},        /* I2S3ext SD */
    {GPIOC, 12, "PC12"},        /* SPI3 SD */
    {GPIOB, 12, "PB12"},        /* SPI2 WS */
    {GPIOB, 13, "PB13"},        /* SPI2 CK */
    {GPIOB, 14, "PB14"},        /* I2S2ext SD */
    {GPIOB, 15, "PB15"},        /* SPI2 SD */
    {GPIOC, 2, "PC2"},          /* I2S2ext SD (other) */
    {GPIOB, 10, "PB10"},        /* SPI2 CK (other) */
    {GPIOC, 3, "PC3"},          /* SPI2 SD (other); OpenRTX's RX audio input (AIN_RTX) */
};
#define NPINS (sizeof PINS / sizeof PINS[0])
enum { P_A15, P_C10, P_C11, P_C12, P_B12, P_B13, P_B14, P_B15, P_C2, P_B10, P_C3, P_NONE = 0xFF };

/* a pin's census: F follows the pulls (nothing drives it), H or L driven, T toggles, ? else */
typedef struct {
    char cls;
    uint32_t edges;             /* edges a ms (the larger of the two pulls) */
} cen_t;

typedef struct {
    const char *name;
    SPI_TypeDef *spi, *ext;
    uint32_t rcc_bit, rst_bit;  /* RCC->APB1ENR, APB1RSTR */
    uint8_t ck, ws, sd, ex;     /* PINS indexes */
    uint8_t af_main, af_ext;
    DMA_Stream_TypeDef *dma, *dma_ext;
    uint8_t ch, ch_ext;
} port_t;

static const port_t PORTS[] = {
    {"SPI3", SPI3, I2S3ext, RCC_APB1ENR_SPI3EN, RCC_APB1RSTR_SPI3RST, P_C10, P_A15, P_C12, P_C11, 6, 5,
     DMA1_Stream0, DMA1_Stream2, 0, 2},
    {"SPI2", SPI2, NULL, RCC_APB1ENR_SPI2EN, RCC_APB1RSTR_SPI2RST, P_B13, P_B12, P_B15, P_NONE, 5, 0,
     DMA1_Stream3, NULL, 0, 0},
};
#define NPORTS (sizeof PORTS / sizeof PORTS[0])

typedef struct {
    char tag[4];
    uint16_t cfg;               /* I2SCFGR: I2SSTD, PCMSYNC, DATLEN, CHLEN */
    uint8_t words;              /* DR reads a frame */
    uint8_t chlen32;
} fmt_t;

static const fmt_t FMTS[] = {
    {"P16", 0, 2, 0},                                   /* Philips, 16-bit channels */
    {"P32", SPI_I2SCFGR_CHLEN, 2, 1},                   /* Philips, 16 bits in 32-bit channels */
    {"M32", SPI_I2SCFGR_I2SSTD_0 | SPI_I2SCFGR_CHLEN, 2, 1},   /* MSB justified */
    {"S16", SPI_I2SCFGR_I2SSTD_0 | SPI_I2SCFGR_I2SSTD_1, 1, 0}, /* PCM, short sync */
};
#define NFMTS (sizeof FMTS / sizeof FMTS[0])

/* the codec settings the master captures try: OpenRTX's receive (c9), that with the other line
   input bit (cb), OpenRTX's mic transmit (c1) and line-in transmit (83) */
static const uint8_t E0S[] = {0xC9, 0xCB, 0xC1, 0x83};
#define NE0S (sizeof E0S / sizeof E0S[0])

/* DMA buffers go in .bss2, the 128 KB RAM: .bss is in the 64 KB CCM RAM on this radio
   (OpenRTX's linker_script_MDx.ld), and no DMA reaches CCM. In .bss, every capture of the first
   flash (2026-11-13) stopped after one halfword (n=1): the DMA's transfer error. */
__attribute__((section(".bss2"))) static int16_t cap[NCAP], capx[NCAP];
static char line[LINE];
static cen_t census[2][NPINS];  /* [0]: E0 c9, [1]: E0 c8 */

static void say(const i2sp_ops *o)
{
    o->say(o->ctx, line);
}

static void cyc_on(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

/* a pin's mode, pull and AF (two bits, two bits, four bits), as one read-modify-write */
typedef struct {
    uint32_t mode, pull, af;
} pinsave_t;

static void pin_get(const pin_t *p, pinsave_t *s)
{
    unsigned n = p->pin;
    s->mode = (p->port->MODER >> (2 * n)) & 3u;
    s->pull = (p->port->PUPDR >> (2 * n)) & 3u;
    s->af = (p->port->AFR[n >> 3] >> (4 * (n & 7))) & 15u;
}

static void pin_set(const pin_t *p, const pinsave_t *s)
{
    unsigned n = p->pin;
    __disable_irq();
    p->port->AFR[n >> 3] = (p->port->AFR[n >> 3] & ~(15u << (4 * (n & 7)))) | (s->af << (4 * (n & 7)));
    p->port->PUPDR = (p->port->PUPDR & ~(3u << (2 * n))) | (s->pull << (2 * n));
    p->port->MODER = (p->port->MODER & ~(3u << (2 * n))) | (s->mode << (2 * n));
    __enable_irq();
}

static void busy_us(unsigned us)
{
    uint32_t t0 = DWT->CYCCNT, span = SystemCoreClock / 1000000u * us;
    while (DWT->CYCCNT - t0 < span) {
    }
}

/* edges a ms on a pin over ms, and its share of high samples (per mille) */
static uint32_t watch(const pin_t *p, unsigned ms, uint32_t *high_pm)
{
    uint32_t m = 1u << p->pin, t0 = DWT->CYCCNT, span = SystemCoreClock / 1000u * ms;
    uint32_t last = p->port->IDR & m, n = 0, hi = 0, e = 0;
    while (DWT->CYCCNT - t0 < span) {
        uint32_t v = p->port->IDR & m;
        e += v != last;
        hi += v != 0;
        last = v;
        n++;
    }
    *high_pm = n ? (uint32_t)((uint64_t)hi * 1000u / n) : 0;
    return e / ms;
}

static void census_pin(const pin_t *p, cen_t *c)
{
    pinsave_t sv, t = {0, 1, 0};
    uint32_t hu, hd, eu, ed;
    pin_get(p, &sv);
    pin_set(p, &t);                       /* input, pull-up */
    busy_us(50);
    eu = watch(p, CENSUS_MS, &hu);
    t.pull = 2;                           /* pull-down */
    pin_set(p, &t);
    busy_us(50);
    ed = watch(p, CENSUS_MS, &hd);
    pin_set(p, &sv);
    c->edges = eu > ed ? eu : ed;
    if (eu > 2 || ed > 2)
        c->cls = 'T';
    else if (hu > 990 && hd < 10)
        c->cls = 'F';
    else if (hu > 990 && hd > 990)
        c->cls = 'H';
    else if (hu < 10 && hd < 10)
        c->cls = 'L';
    else
        c->cls = '?';
}

static void do_census(const i2sp_ops *o, int which, uint8_t e0)
{
    unsigned i;
    size_t len;
    o->c6000_write(o->ctx, 0xE0, e0);
    o->sleep_ms(o->ctx, 20);
    snprintf(line, LINE, "census e0=%02x", e0);
    for (i = 0; i < NPINS; i++) {
        census_pin(&PINS[i], &census[which][i]);
        len = strlen(line);
        if (census[which][i].cls == 'T')
            snprintf(line + len, LINE - len, " %s=T%lu", PINS[i].name, (unsigned long)census[which][i].edges);
        else
            snprintf(line + len, LINE - len, " %s=%c", PINS[i].name, census[which][i].cls);
    }
    say(o);
}

static unsigned dma_index(const DMA_Stream_TypeDef *s)
{
    return ((uint32_t)s - (uint32_t)DMA1_Stream0) / 0x18u;
}

static const uint8_t DMA_SH[4] = {0, 6, 16, 22};

/* a stream's flags, as LISR/HISR hold them: bit 0 FEIF, 2 DMEIF, 3 TEIF, 4 HTIF, 5 TCIF */
static unsigned dma_flags(const DMA_Stream_TypeDef *s)
{
    unsigned i = dma_index(s);
    return i < 4 ? (DMA1->LISR >> DMA_SH[i]) & 0x3Du : (DMA1->HISR >> DMA_SH[i - 4]) & 0x3Du;
}

static unsigned last_flags;     /* the last capture's DMA flags (ext stream's << 8) */
static uint16_t last_sr;        /* and its SPI SR before the port was stopped */

static void dma_clear(const DMA_Stream_TypeDef *s)
{
    unsigned i = dma_index(s);
    if (i < 4)
        DMA1->LIFCR = 0x3Du << DMA_SH[i];
    else
        DMA1->HIFCR = 0x3Du << DMA_SH[i - 4];
}

static void dma_off(DMA_Stream_TypeDef *s)
{
    unsigned t = 0;
    s->CR &= ~DMA_SxCR_EN;
    while ((s->CR & DMA_SxCR_EN) && t++ < 100000u) {
    }
    dma_clear(s);
}

static void dma_rx(DMA_Stream_TypeDef *s, uint8_t ch, volatile uint32_t *dr, int16_t *buf, unsigned n)
{
    dma_off(s);
    s->PAR = (uint32_t)dr;
    s->M0AR = (uint32_t)buf;
    s->NDTR = n;
    s->FCR = 0;
    s->CR = ((uint32_t)ch << DMA_SxCR_CHSEL_Pos) | DMA_SxCR_PL_1 | DMA_SxCR_MSIZE_0 | DMA_SxCR_PSIZE_0 |
            DMA_SxCR_MINC;
    s->CR |= DMA_SxCR_EN;
}

typedef struct {
    uint32_t cr, ndtr, par, m0ar, m1ar, fcr;
} dmasave_t;

static void dma_save(const DMA_Stream_TypeDef *s, dmasave_t *v)
{
    v->cr = s->CR, v->ndtr = s->NDTR, v->par = s->PAR, v->m0ar = s->M0AR, v->m1ar = s->M1AR, v->fcr = s->FCR;
}

static void dma_restore(DMA_Stream_TypeDef *s, const dmasave_t *v)
{
    dma_off(s);
    s->NDTR = v->ndtr, s->PAR = v->par, s->M0AR = v->m0ar, s->M1AR = v->m1ar, s->FCR = v->fcr;
    s->CR = v->cr & ~DMA_SxCR_EN;
}

static int pll_on(void)
{
    unsigned t = 0;
    if (RCC->CR & RCC_CR_PLLI2SRDY)
        return 0;
    RCC->CFGR &= ~RCC_CFGR_I2SSRC;
    RCC->PLLI2SCFGR = (2u << RCC_PLLI2SCFGR_PLLI2SR_Pos) | (256u << RCC_PLLI2SCFGR_PLLI2SN_Pos);
    RCC->CR |= RCC_CR_PLLI2SON;
    while (!(RCC->CR & RCC_CR_PLLI2SRDY) && t++ < 1000000u) {
    }
    return 1;
}

/* One capture on a port: master (the MCU clocks it) or slave (the C6000 does). Returns the
   halfwords that came on SD; *gx those on ext SD. */
static unsigned capture(const i2sp_ops *o, const port_t *p, const fmt_t *f, int master, unsigned fs,
                        unsigned *gx)
{
    pinsave_t sv[4], t;
    const uint8_t idx[4] = {p->ck, p->ws, p->sd, p->ex};
    dmasave_t ds, dsx;
    unsigned i, waited = 0, ms, div2;
    *gx = 0;
    memset(cap, 0, sizeof cap);
    memset(capx, 0, sizeof capx);
    RCC->AHB1ENR |= RCC_AHB1ENR_DMA1EN;
    dma_save(p->dma, &ds);
    if (p->dma_ext)
        dma_save(p->dma_ext, &dsx);
    RCC->APB1ENR |= p->rcc_bit;
    RCC->APB1RSTR |= p->rst_bit;
    RCC->APB1RSTR &= ~p->rst_bit;
    for (i = 0; i < 4; i++) {
        if (idx[i] == P_NONE)
            continue;
        pin_get(&PINS[idx[i]], &sv[i]);
        t.mode = 2, t.pull = 0, t.af = i == 3 ? p->af_ext : p->af_main;
        pin_set(&PINS[idx[i]], &t);
    }
    dma_rx(p->dma, p->ch, &p->spi->DR, cap, NCAP);
    if (p->ext) {
        dma_rx(p->dma_ext, p->ch_ext, &p->ext->DR, capx, NCAP);
        p->ext->I2SCFGR = SPI_I2SCFGR_I2SMOD | SPI_I2SCFGR_I2SCFG_0 | f->cfg;    /* slave receive */
        p->ext->CR2 = SPI_CR2_RXDMAEN;
        p->ext->I2SCFGR |= SPI_I2SCFGR_I2SE;
    }
    div2 = I2SCLK / ((f->chlen32 ? 64u : 32u) * fs);
    p->spi->I2SPR = master ? ((div2 >> 1) | ((div2 & 1u) << 8)) : 2u;
    p->spi->I2SCFGR = SPI_I2SCFGR_I2SMOD | (master ? (SPI_I2SCFGR_I2SCFG_0 | SPI_I2SCFGR_I2SCFG_1)
                                                   : SPI_I2SCFGR_I2SCFG_0) | f->cfg;
    p->spi->CR2 = SPI_CR2_RXDMAEN;
    p->spi->I2SCFGR |= SPI_I2SCFGR_I2SE;
    ms = NCAP * 1000u / (fs * f->words) * 2u + 100u;
    while (waited < ms && (p->dma->NDTR || (p->dma_ext && p->dma_ext->NDTR))) {
        o->sleep_ms(o->ctx, 5);
        waited += 5;
    }
    p->spi->I2SCFGR &= ~SPI_I2SCFGR_I2SE;
    if (p->ext)
        p->ext->I2SCFGR &= ~SPI_I2SCFGR_I2SE;
    i = NCAP - p->dma->NDTR;
    if (p->dma_ext)
        *gx = NCAP - p->dma_ext->NDTR;
    last_flags = dma_flags(p->dma) | (p->dma_ext ? dma_flags(p->dma_ext) << 8 : 0u);
    last_sr = (uint16_t)p->spi->SR;
    p->spi->CR2 = 0;
    p->spi->I2SCFGR = 0;
    if (p->ext)
        p->ext->CR2 = 0, p->ext->I2SCFGR = 0;
    dma_restore(p->dma, &ds);
    if (p->dma_ext)
        dma_restore(p->dma_ext, &dsx);
    {
        unsigned j;
        for (j = 0; j < 4; j++)
            if (idx[j] != P_NONE)
                pin_set(&PINS[idx[j]], &sv[j]);
    }
    RCC->APB1RSTR |= p->rst_bit;
    RCC->APB1RSTR &= ~p->rst_bit;
    RCC->APB1ENR &= ~p->rcc_bit;
    return i;
}

static void report(const fmt_t *f, unsigned fs, unsigned got, unsigned gx, int ext)
{
    i2sp_stats s;
    unsigned ch, nch = f->words;
    size_t len = strlen(line);
    char tag[3] = {'a', '0', 0};
    snprintf(line + len, LINE - len, " n=%u f=%03x sr=%03x", got, last_flags, (unsigned)last_sr);
    for (ch = 0; ch < nch; ch++) {
        tag[1] = (char)('0' + ch);
        i2sp_analyse(cap + ch, got / nch, nch, fs, &s);
        i2sp_format(line, LINE, tag, &s);
    }
    if (!ext)
        return;
    len = strlen(line);
    snprintf(line + len, LINE - len, " x=%u", gx);
    tag[0] = 'b';
    for (ch = 0; ch < nch; ch++) {
        tag[1] = (char)('0' + ch);
        i2sp_analyse(capx + ch, gx / nch, nch, fs, &s);
        i2sp_format(line, LINE, tag, &s);
    }
}

#define MUX_PORT GPIOD          /* RX_AUDIO_MUX: high, the AT1846S's audio to the C6000 */
#define MUX_PIN 9u

static void mux(int on)
{
    MUX_PORT->BSRR = on ? (1u << MUX_PIN) : (1u << (MUX_PIN + 16));
}

static void captures(const i2sp_ops *o, uint8_t e0_0)
{
    unsigned pi, ei, mi, fi, si, got, gx;
    static const unsigned FS[2] = {8000, 16000};
    for (pi = 0; pi < NPORTS; pi++) {
        const port_t *p = &PORTS[pi];
        int which, slave = -1, master;
        unsigned sfs = 0;
        for (which = 1; which >= 0 && slave < 0; which--)
            if (census[which][p->ck].cls == 'T' && census[which][p->ws].cls == 'T')
                slave = which, sfs = census[which][p->ws].edges * 500u;     /* edges a ms / 2, a s */
        master = census[0][p->ck].cls == 'F' && census[0][p->ws].cls == 'F';
        if (slave >= 0 && (sfs < 4000u || sfs > 96000u))
            sfs = 8000u;
        if (slave < 0 && !master) {
            snprintf(line, LINE, "skip %s: %s=%c %s=%c (driven, and no clock seen)", p->name,
                     PINS[p->ck].name, census[0][p->ck].cls, PINS[p->ws].name, census[0][p->ws].cls);
            say(o);
            continue;
        }
        for (mi = 0; mi < 2; mi++) {
            mux(mi == 0);
            for (ei = 0; ei < (slave >= 0 ? 1u : NE0S); ei++) {
                uint8_t e0 = slave >= 0 ? (slave ? 0xC8 : 0xC9) : E0S[ei];
                o->c6000_write(o->ctx, 0xE0, e0);
                o->sleep_ms(o->ctx, 20);
                for (fi = 0; fi < NFMTS; fi++) {
                    for (si = 0; si < (slave >= 0 ? 1u : 2u); si++) {
                        unsigned fs = slave >= 0 ? sfs : FS[si];
                        if (o->stop(o->ctx))
                            goto out;
                        got = capture(o, p, &FMTS[fi], slave < 0, fs, &gx);
                        snprintf(line, LINE, "c %s %s e0=%02x mx=%u %s %u", p->name, slave >= 0 ? "s" : "m",
                                 e0, mi == 0, FMTS[fi].tag, fs);
                        report(&FMTS[fi], fs, got, gx, p->ext != NULL);
                        say(o);
                    }
                }
            }
        }
    }
out:
    mux(1);
    o->c6000_write(o->ctx, 0xE0, e0_0);
}

void i2sp_pass(const i2sp_ops *o)
{
    uint8_t e0 = o->c6000_read(o->ctx, 0xE0), r26 = o->c6000_read(o->ctx, 0x26);
    int pll;
    unsigned got;
    i2sp_stats s;
    cyc_on();
    snprintf(line, LINE, "i2sprobe start e0=%02x e1=%02x e2=%02x e3=%02x e4=%02x e5=%02x 06=%02x 26=%02x mux=%u "
             "pll=%u clk=%lu cap=%08lx", e0, o->c6000_read(o->ctx, 0xE1), o->c6000_read(o->ctx, 0xE2),
             o->c6000_read(o->ctx, 0xE3), o->c6000_read(o->ctx, 0xE4), o->c6000_read(o->ctx, 0xE5),
             o->c6000_read(o->ctx, 0x06), r26, (unsigned)((MUX_PORT->ODR >> MUX_PIN) & 1u),
             (unsigned)((RCC->CR & RCC_CR_PLLI2SRDY) != 0), (unsigned long)SystemCoreClock,
             (unsigned long)(uint32_t)cap);
    say(o);
    if (((uint32_t)cap >> 24) == 0x10u) {
        o->say(o->ctx, "i2sprobe end (capture buffer in CCM, which no DMA reaches)");
        return;
    }
    o->c6000_write(o->ctx, 0x26, 0xFD);   /* the C6000's FM receive on (OpenRTX: radio_enableAfOutput) */
    do_census(o, 0, 0xC9);
    if (!o->stop(o->ctx))
        do_census(o, 1, 0xC8);
    o->c6000_write(o->ctx, 0xE0, e0);
    pll = pll_on();
    if (!o->stop(o->ctx))
        captures(o, e0);
    if (pll)
        RCC->CR &= ~RCC_CR_PLLI2SON;
    if (!o->stop(o->ctx) && o->adc_capture) {
        got = o->adc_capture(o->ctx, cap, NCAP, 8000);
        snprintf(line, LINE, "adc PC3 8000 n=%u", got);
        i2sp_analyse(cap, got, 1, 8000, &s);
        i2sp_format(line, LINE, "a", &s);
        say(o);
    }
    o->c6000_write(o->ctx, 0x26, r26);
    o->c6000_write(o->ctx, 0xE0, e0);
    snprintf(line, LINE, "i2sprobe end%s", o->stop(o->ctx) ? " (stopped)" : "");
    say(o);
}

#else

void i2sp_pass(const i2sp_ops *o)
{
    o->say(o->ctx, "i2sprobe: the radio only");
}

#endif
