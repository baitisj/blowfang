/* pluto_chan: several narrowband FM channels from one IQ stream, small enough for the Pluto's ARM.
 *
 *   iio_readdev -b 131072 cf-ad9361-lpc voltage0 voltage1 | pluto_chan LO_HZ RATE HZ1 [HZ2 ...]
 *
 * stdin: int16 I,Q pairs at RATE (the AD9361's 12 bits, full scale 2048), centred on LO_HZ.
 * stdout: one record each 0.1 s: N float32 levels (dBFS, the channel's power), then N x 800 int16
 * of 8 kHz FM audio, channel after channel (WAV_FULL_DEV Hz of deviation = 32767). Only audio
 * crosses the USB link: 16 kB/s a channel.
 *
 * Per channel, a mix and three decimating FIRs that compute only the outputs they keep:
 *   mix: the channel to 0 Hz with a table of one LO period (the offset rounded to 1 kHz when its
 *      period would be long; FM does not mind), in runs the compiler vectorizes;
 *   A: low-pass, real taps, RATE -> RATE/QA (160 kS/s at 2.4 MS/s);
 *   B: low-pass +-8 kHz, -> 32 kS/s; discriminator;
 *   C: audio low-pass 3.6 kHz, -> 8 kS/s.
 * With -f, stdout carries squelch-gated overs instead, each a HamFLAC stream (flacout.cpp).
 * The channels are shared out over THREADS worker threads (default 2: the Pluto's two A9 cores).
 * Build: cc -O3 -ffast-math (add -mfpu=neon -mfloat-abi=hard -mcpu=cortex-a9 for the Pluto, and
 * -static: the Pluto's glibc is older than the build box's). Plain C, no libraries but libm.
 * Same filters and scaling as tools/pluto_bench.py ChannelSink, so the two can be compared.
 */
#include <math.h>
#include <pthread.h>
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
#define BLK 16384                 /* input samples a read (the mixed buffers stay in L2) */
#define MAXP 4800                 /* longest LO table */

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
    float ha[LA], hb[LB], hc[LC];  /* reversed (symmetric anyway) */
    float *lr_, *li_; int lp, lk;  /* LO table (period lp) and the position in it */
    float *mr, *mi; int nm, pa;    /* mixed input: samples held, the next stage-A window */
    float *br, *bi; int nb, pb;    /* stage B input ring (linear, compacted) */
    float lr, li;                  /* discriminator: last sample */
    float *c; int nc, pc;          /* stage C input */
    double pacc; int pn;           /* power over 0.1 s at FS1 */
    float level;
    int16_t aud[FS2]; int na;      /* up to 1 s waiting for the slowest channel */
} chan_t;

static int nch;
static chan_t ch[MAXCH];
static float *xr, *xi;             /* the block, float */

static void chan_init(chan_t *c, double hz, double lo, int rate)
{
    memset(c, 0, sizeof *c);
    c->hz = hz; c->off = hz - lo;
    c->qa = rate / 160000; c->qb = 160000 / FS1; c->qc = FS1 / FS2;
    firwin(c->ha, LA, 12000, rate);              /* pass +-8 kHz, alias-free to 160k - 8k */
    firwin(c->hb, LB, 8000, 160000);
    firwin(c->hc, LC, 0.45 * FS2, FS1);
    long off = lrint(c->off), g = rate, t = labs(off);
    while (t) { long r = g % t; g = t; t = r; }  /* gcd(rate, |off|) */
    if (rate / g > MAXP) {                       /* round to 1 kHz: period at most rate/1000 */
        off = lrint(c->off / 1000.0) * 1000;
        g = rate; t = labs(off);
        while (t) { long r = g % t; g = t; t = r; }
    }
    c->lp = off ? (int)(rate / g) : 1;
    c->lr_ = malloc(c->lp * sizeof(float)); c->li_ = malloc(c->lp * sizeof(float));
    for (int k = 0; k < c->lp; k++) {
        c->lr_[k] = (float)cos(-2 * M_PI * (double)off * k / rate);
        c->li_[k] = (float)sin(-2 * M_PI * (double)off * k / rate);
    }
    c->mr = calloc(LA + BLK, sizeof(float)); c->mi = calloc(LA + BLK, sizeof(float));
    c->nm = LA - 1;                              /* zero history */
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

/* mix the block (xr/xi, n samples) into the channel's buffer, then stage A on it */
static void stage_a(chan_t *c, const float *restrict xr_, const float *restrict xi_, int n)
{
    float *restrict mr = c->mr + c->nm, *restrict mi = c->mi + c->nm;
    for (int s0 = 0; s0 < n;) {                  /* runs inside one LO period: no modulo inside */
        int run = c->lp - c->lk;
        if (run > n - s0)
            run = n - s0;
        const float *restrict lr = c->lr_ + c->lk, *restrict li = c->li_ + c->lk;
        const float *restrict ar = xr_ + s0, *restrict ai = xi_ + s0;
        float *restrict orr = mr + s0, *restrict oi = mi + s0;
        for (int k = 0; k < run; k++) {
            orr[k] = ar[k] * lr[k] - ai[k] * li[k];
            oi[k] = ar[k] * li[k] + ai[k] * lr[k];
        }
        s0 += run;
        c->lk += run;
        if (c->lk == c->lp)
            c->lk = 0;
    }
    c->nm += n;
    const float *restrict h = c->ha;
    while (c->pa + LA <= c->nm) {
        const float *restrict r = c->mr + c->pa, *restrict i = c->mi + c->pa;
        float sr = 0, si = 0;
        for (int k = 0; k < LA; k++) {
            sr += h[k] * r[k];
            si += h[k] * i[k];
        }
        c->br[c->nb] = sr;
        c->bi[c->nb] = si;
        c->nb++;
        c->pa += c->qa;
    }
    int keep = c->nm - c->pa;                    /* compact: from the next window on */
    memmove(c->mr, c->mr + c->pa, keep * sizeof(float));
    memmove(c->mi, c->mi + c->pa, keep * sizeof(float));
    c->nm = keep; c->pa = 0;
    stage_b(c);
}

/* the worker threads: each takes the channels j with j % nthr == its number, block by block */
static int nthr = 2, nblk, done;
static pthread_barrier_t bar_go, bar_done;

static void *worker(void *arg)
{
    int t = (int)(long)arg;
    for (;;) {
        pthread_barrier_wait(&bar_go);
        if (done)
            return NULL;
        for (int j = t; j < nch; j += nthr)
            stage_a(&ch[j], xr, xi, nblk);
        pthread_barrier_wait(&bar_done);
    }
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
    const char *te = getenv("THREADS");
    if (te)
        nthr = atoi(te);
    if (nthr < 1) nthr = 1;
    if (nthr > nch) nthr = nch;
    xr = calloc(BLK, sizeof(float));
    xi = calloc(BLK, sizeof(float));
    static int16_t raw[2 * BLK];
    pthread_t th[MAXCH];
    pthread_barrier_init(&bar_go, NULL, nthr + 1);
    pthread_barrier_init(&bar_done, NULL, nthr + 1);
    for (int t = 0; t < nthr; t++)
        pthread_create(&th[t], NULL, worker, (void *)(long)t);
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    long total = 0;
    size_t got;
    while ((got = fread(raw, 4, BLK, stdin)) > 0) {
        for (size_t s = 0; s < got; s++) {
            xr[s] = raw[2 * s] * (1.0f / 2048);
            xi[s] = raw[2 * s + 1] * (1.0f / 2048);
        }
        nblk = (int)got;
        pthread_barrier_wait(&bar_go);           /* the workers run the channels */
        pthread_barrier_wait(&bar_done);
        while (1) {                              /* a record when every channel has 0.1 s */
            int ok = 1;
            for (int j = 0; j < nch; j++)
                ok &= ch[j].na >= FS2 / 10;
            if (!ok)
                break;
            emit();
        }
        total += (long)got;
    }
    done = 1;
    pthread_barrier_wait(&bar_go);
    for (int t = 0; t < nthr; t++)
        pthread_join(th[t], NULL);
    if (gated)
        flac_finish();
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double el = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;
    fprintf(stderr, "pluto_chan: %d channels on %d threads, %.2f s of input in %.2f s (%.0f%% of real time)\n",
            nch, nthr, total / (double)rate, el, 100 * el / (total / (double)rate));
    return 0;
}
