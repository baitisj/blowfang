/* pluto_chan: several narrowband FM channels from one IQ stream, small enough for the Pluto's ARM.
 *
 *   iio_readdev -b 131072 cf-ad9361-lpc voltage0 voltage1 | pluto_chan LO_HZ RATE HZ1 [HZ2 ...]
 *
 * stdin: int16 I,Q pairs at RATE (the AD9361's 12 bits, full scale 2048), centred on LO_HZ.
 * stdout: one record each 0.1 s: N float32 levels (dBFS, the channel's power), then N x 800 int16
 * of 8 kHz FM audio, channel after channel (WAV_FULL_DEV Hz of deviation = 32767). Only audio
 * crosses the USB link: 16 kB/s a channel.
 *
 * Per channel, three decimating FIRs that compute only the outputs they keep:
 *   A: complex band-pass (the channel's own frequency folded into the taps), RATE -> RATE/QA
 *      (160 kS/s at 2.4 MS/s), then the LO taken out at the kept outputs only;
 *   B: low-pass +-8 kHz, -> 32 kS/s; discriminator;
 *   C: audio low-pass 3.6 kHz, -> 8 kS/s.
 * With -f, stdout carries squelch-gated overs instead, each a HamFLAC stream (flacout.cpp).
 * Build: cc -O3 -ffast-math (add -mfpu=neon -mfloat-abi=hard -mcpu=cortex-a9 for the Pluto, and
 * -static: the Pluto's glibc is older than the build box's). Plain C, no libraries but libm.
 * Same filters and scaling as tools/pluto_bench.py ChannelSink, so the two can be compared.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define FS1 32000                 /* after stage B */
#define FS2 8000                  /* audio out */
#define LA 63                     /* stage A taps */
#define LB 95                     /* stage B taps */
#define LC 63                     /* stage C taps */
#define WAV_FULL_DEV 5000.0f
#define MAXCH 16
#define BLK 65536                 /* input samples a read */

static double sinc(double x) { return x == 0 ? 1.0 : sin(M_PI * x) / (M_PI * x); }

/* windowed-sinc low-pass, cutoff fc (Hz) at fs, DC gain 1 (Hamming, as scipy firwin) */
static void firwin(float *h, int n, double fc, double fs)
{
    double s = 0, w[512];
    for (int k = 0; k < n; k++) {
        double m = k - (n - 1) / 2.0;
        w[k] = 2 * fc / fs * sinc(2 * fc / fs * m) * (0.54 - 0.46 * cos(2 * M_PI * k / (n - 1)));
        s += w[k];
    }
    for (int k = 0; k < n; k++)
        h[k] = (float)(w[k] / s);
}

typedef struct {
    double hz, off;
    int qa, qb, qc;
    float ar[LA], ai[LA];          /* stage A taps, reversed, band-pass at off */
    float hb[LB], hc[LC];          /* reversed (symmetric anyway) */
    long pa;                       /* stage A: the next kept output, as an input index */
    double wl;                     /* LO step a kept stage-A output, rad */
    double phl;                    /* LO phase at the next kept output */
    float *br, *bi; int nb, pb;    /* stage B input ring (linear, compacted) */
    float lr, li;                  /* discriminator: last sample */
    float *c; int nc, pc;          /* stage C input */
    double pacc; int pn;           /* power over 0.1 s at FS1 */
    float level;
    int16_t aud[FS2]; int na;      /* up to 1 s waiting for the slowest channel */
} chan_t;

static int nch;
static chan_t ch[MAXCH];
static float *xr, *xi;             /* input history LA-1 + BLK, float */
static long base;                  /* input index of xr[0] */

static void chan_init(chan_t *c, double hz, double lo, int rate)
{
    memset(c, 0, sizeof *c);
    c->hz = hz; c->off = hz - lo;
    c->qa = rate / 160000; c->qb = 160000 / FS1; c->qc = FS1 / FS2;
    float h[LA];
    firwin(h, LA, 12000, rate);                  /* pass +-8 kHz, alias-free to 160k - 8k */
    for (int k = 0; k < LA; k++) {               /* taps reversed: ar[j] multiplies x[n - (LA-1) + j] */
        double ph = 2 * M_PI * c->off * k / rate;
        c->ar[LA - 1 - k] = (float)(h[k] * cos(ph));
        c->ai[LA - 1 - k] = (float)(h[k] * sin(ph));
    }
    firwin(c->hb, LB, 8000, 160000);
    firwin(c->hc, LC, 0.45 * FS2, FS1);
    c->pa = LA - 1;
    c->wl = -2 * M_PI * c->off * c->qa / rate;
    c->phl = -2 * M_PI * c->off * (LA - 1) / rate;
    c->br = calloc(BLK + LB, sizeof(float)); c->bi = calloc(BLK + LB, sizeof(float));
    c->nb = LB - 1;                              /* zero history */
    c->c = calloc(BLK + LC, sizeof(float)); c->nc = LC - 1;
    c->lr = 1; c->li = 0;
}

static void emit(void);
static int gated;                  /* -f: squelch-gated HamFLAC overs (flacout.cpp) */
void flac_tick(int ch, int nch, const int16_t *aud, int n, float level);   /* flacout.cpp */
void flac_finish(void);

static void stage_c(chan_t *c, float d)
{
    c->c[c->nc++] = d;
    while (c->pc + LC <= c->nc) {
        const float *x = c->c + c->pc;
        float a = 0;
        for (int k = 0; k < LC; k++)
            a += c->hc[k] * x[k];
        float v = a / WAV_FULL_DEV * 32767.0f;
        if (c->na < FS2)
            c->aud[c->na++] = (int16_t)(v > 32767 ? 32767 : v < -32767 ? -32767 : lrintf(v));
        c->pc += c->qc;
    }
    if (c->pc > 4096) {                          /* compact */
        memmove(c->c, c->c + c->pc, (c->nc - c->pc) * sizeof(float));
        c->nc -= c->pc; c->pc = 0;
    }
}

static void stage_b(chan_t *c)
{
    while (c->pb + LB <= c->nb) {
        const float *r = c->br + c->pb, *i = c->bi + c->pb;
        float sr = 0, si = 0;
        for (int k = 0; k < LB; k++) {
            sr += c->hb[k] * r[k];
            si += c->hb[k] * i[k];
        }
        c->pb += c->qb;
        c->pacc += sr * sr + si * si;
        if (++c->pn == FS1 / 10) {
            c->level = (float)(10 * log10(c->pacc / c->pn + 1e-20));
            c->pacc = 0; c->pn = 0;
        }
        /* discriminator: angle(x * conj(last)) */
        float pr = sr * c->lr + si * c->li, pi = si * c->lr - sr * c->li;
        c->lr = sr; c->li = si;
        stage_c(c, atan2f(pi, pr) * (FS1 / (2 * (float)M_PI)));
    }
    if (c->pb > 4096) {
        memmove(c->br, c->br + c->pb, (c->nb - c->pb) * sizeof(float));
        memmove(c->bi, c->bi + c->pb, (c->nb - c->pb) * sizeof(float));
        c->nb -= c->pb; c->pb = 0;
    }
}

/* the block xr/xi holds input indices base .. base + n - 1 */
static void stage_a(chan_t *c, long n)
{
    while (c->pa < base + n) {
        const float *r = xr + (c->pa - (LA - 1) - base), *i = xi + (c->pa - (LA - 1) - base);
        float sr = 0, si = 0;
        for (int k = 0; k < LA; k++) {
            sr += c->ar[k] * r[k] - c->ai[k] * i[k];
            si += c->ar[k] * i[k] + c->ai[k] * r[k];
        }
        float lr = (float)cos(c->phl), li = (float)sin(c->phl);
        c->phl = fmod(c->phl + c->wl, 2 * M_PI);
        c->br[c->nb] = sr * lr - si * li;
        c->bi[c->nb] = sr * li + si * lr;
        c->nb++;
        c->pa += c->qa;
    }
    stage_b(c);
}

static void emit(void)
{
    if (gated) {
        for (int j = 0; j < nch; j++) {
            flac_tick(j, nch, ch[j].aud, FS2 / 10, ch[j].level);
            memmove(ch[j].aud, ch[j].aud + FS2 / 10, (ch[j].na - FS2 / 10) * sizeof(int16_t));
            ch[j].na -= FS2 / 10;
        }
        return;
    }
    float lv[MAXCH];
    for (int j = 0; j < nch; j++)
        lv[j] = ch[j].level;
    fwrite(lv, sizeof(float), nch, stdout);
    for (int j = 0; j < nch; j++) {
        fwrite(ch[j].aud, sizeof(int16_t), FS2 / 10, stdout);
        memmove(ch[j].aud, ch[j].aud + FS2 / 10, (ch[j].na - FS2 / 10) * sizeof(int16_t));
        ch[j].na -= FS2 / 10;
    }
    fflush(stdout);
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "-f")) {
        gated = 1;
        argv++; argc--;
    }
    if (argc < 4) {
        fprintf(stderr, "usage: %s [-f] LO_HZ RATE HZ1 [HZ2 ...]  (int16 IQ on stdin)\n"
                "  -f: squelch-gated overs, each a HamFLAC stream (flacout.cpp says the format)\n", argv[0]);
        return 2;
    }
    double lo = atof(argv[1]);
    int rate = atoi(argv[2]);
    if (rate % 160000) {
        fprintf(stderr, "pluto_chan: RATE must be a multiple of 160000\n");
        return 2;
    }
    nch = argc - 3;
    if (nch > MAXCH) {
        fprintf(stderr, "pluto_chan: at most %d channels\n", MAXCH);
        return 2;
    }
    for (int j = 0; j < nch; j++) {
        double hz = atof(argv[3 + j]);
        if (fabs(hz - lo) > rate / 2.0 - 40e3) {
            fprintf(stderr, "pluto_chan: %.0f is outside the stream\n", hz);
            return 2;
        }
        chan_init(&ch[j], hz, lo, rate);
    }
    xr = calloc(LA - 1 + BLK, sizeof(float));
    xi = calloc(LA - 1 + BLK, sizeof(float));
    static int16_t raw[2 * BLK];
    base = 0;                                    /* xr[0..LA-2] = zero history before index LA-1 */
    long have = LA - 1;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    long total = 0;
    size_t got;
    while ((got = fread(raw, 4, BLK, stdin)) > 0) {
        for (size_t s = 0; s < got; s++) {
            xr[have + s] = raw[2 * s] * (1.0f / 2048);
            xi[have + s] = raw[2 * s + 1] * (1.0f / 2048);
        }
        long n = have + (long)got;
        for (int j = 0; j < nch; j++)
            stage_a(&ch[j], n);
        while (1) {                              /* a record when every channel has 0.1 s */
            int ok = 1;
            for (int j = 0; j < nch; j++)
                ok &= ch[j].na >= FS2 / 10;
            if (!ok)
                break;
            emit();
        }
        memmove(xr, xr + n - (LA - 1), (LA - 1) * sizeof(float));
        memmove(xi, xi + n - (LA - 1), (LA - 1) * sizeof(float));
        base += n - (LA - 1);
        have = LA - 1;
        total += (long)got;
    }
    if (gated)
        flac_finish();
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double el = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;
    fprintf(stderr, "pluto_chan: %d channels, %.2f s of input in %.2f s (%.0f%% of real time)\n",
            nch, total / (double)rate, el, 100 * el / (total / (double)rate));
    return 0;
}
