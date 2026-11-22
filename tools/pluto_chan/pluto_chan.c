/* pluto_chan: several narrowband FM channels from one IQ stream, small enough for the Pluto's ARM.
 *
 *   iio_readdev -b 131072 cf-ad9361-lpc voltage0 voltage1 | pluto_chan LO_HZ RATE HZ1 [HZ2 ...]
 *
 * stdin: int16 I,Q pairs at RATE (the AD9361's 12 bits, full scale 2048), centred on LO_HZ.
 * stdout: one record each 0.1 s: N float32 levels (dBFS, the channel's power), then N x 800 int16
 * of 8 kHz FM audio, channel after channel (WAV_FULL_DEV Hz of deviation = 32767). Only audio
 * crosses the USB link: 16 kB/s a channel.
 *
 * Per channel, decimating FIRs that compute only the outputs they keep. By default (A1MODE 3) a mix
 * from a table of one LO period, then stage A: 63 real taps, RATE -> 160 kS/s, then B and C below.
 * A1MODE 0 and 1 split stage A in two (slower on the Pluto's A9, kept to measure against):
 *   A1: the mix to 0 Hz and a 9-tap triangle (two 5-sample boxcars, CIC order 2) in one pass,
 *      RATE -> 480 kS/s, with the LO folded into the taps (a table per output phase: the offset
 *      rounded to 5 kHz keeps it at most 480 phases; the rest is turned out after A2). The
 *      triangle's nulls sit on the bands that fold onto the channel, -70 dB at 480 kHz +- 8 kHz;
 *   A2: low-pass, 21 real taps, -> 160 kS/s;
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
#include <errno.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

#define FS1 32000                 /* after stage B */
#define FS2 8000                  /* audio out */
#define FSA 480000                /* after stage A1 */
#define L1 9                      /* stage A1: triangle taps */
#define L0 63                     /* stage A, A1MODE=3 */
#define LA 21                     /* stage A2 taps */
#define LB 63                     /* stage B taps */
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
    int q1, qa, qb, qc;
    float h1[L1], ha[LA], hb[LB], hc[LC];  /* symmetric, so reversed is the same */
    float *tr, *ti; int nq, q;     /* A1 taps with the LO in them, a set per output phase */
    int p1;                        /* the next A1 window, as an index into the shared block */
    float *lr_, *li_; int lp, lk;  /* A1MODE=1: the LO table (one period) and the place in it */
    float *mr, *mi, *sr, *si;      /* A1MODE=1: the mixed block and its 5-sample sums */
    float zr, zi, wr, wi; int zn;  /* the residual offset turned out after A2 (a phasor) */
    float h0[L0];                  /* A1MODE=3: one 63-tap stage A at RATE (build 780b) */
    float *l0r, *l0i; int lp0, lk0;/* A1MODE=3: its LO table, offset exact or to 1 kHz */
    float *m0r, *m0i; int nm0, pa0;/* A1MODE=3: the mixed input and the next window */
    float *ar_, *ai_; int na2, pa; /* stage A2 input */
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
    c->q1 = rate / FSA; c->qa = FSA / 160000; c->qb = 160000 / FS1; c->qc = FS1 / FS2;
    for (int k = 0; k < L1; k++)                 /* 1 2 3 4 5 4 3 2 1, over 25 */
        c->h1[k] = (float)(5 - abs(k - 4)) / 25.0f;
    firwin(c->ha, LA, 30000, FSA);               /* pass +-8 kHz, stop by ~100 kHz (160k - 8k folds) */
    firwin(c->hb, LB, 7500, 160000);             /* stop by ~12 kHz: 20 kHz neighbours out */
    firwin(c->hc, LC, 0.45 * FS2, FS1);
    long off = lrint(c->off / 5000.0) * 5000, g = rate, t = labs(off);
    while (t) { long r = g % t; g = t; t = r; }  /* gcd(rate, |off|) */
    int lp = off ? (int)(rate / g) : 1;          /* the LO's period, at most rate / 5000 */
    int a = lp, b = c->q1;
    while (b) { int r = a % b; a = b; b = r; }
    c->nq = lp / a;                              /* the output phases visited: lp / gcd(lp, q1) */
    c->lp = lp;
    c->lr_ = malloc(lp * sizeof(float)); c->li_ = malloc(lp * sizeof(float));
    for (int k = 0; k < lp; k++) {
        c->lr_[k] = (float)cos(-2 * M_PI * (double)off * k / rate);
        c->li_[k] = (float)sin(-2 * M_PI * (double)off * k / rate);
    }
    c->mr = calloc(L1 + BLK, sizeof(float)); c->mi = calloc(L1 + BLK, sizeof(float));
    c->sr = calloc(L1 + BLK, sizeof(float)); c->si = calloc(L1 + BLK, sizeof(float));
    c->tr = malloc(c->nq * L1 * sizeof(float)); c->ti = malloc(c->nq * L1 * sizeof(float));
    for (int q = 0; q < c->nq; q++)
        for (int k = 0; k < L1; k++) {
            long n = ((long)q * c->q1 + k) % lp;
            double ph = -2 * M_PI * (double)off * n / rate;
            c->tr[q * L1 + k] = (float)(c->h1[k] * cos(ph));
            c->ti[q * L1 + k] = (float)(c->h1[k] * sin(ph));
        }
    double res = c->off - off;                   /* |res| <= 2.5 kHz, turned at 160 kS/s */
    c->zr = 1; c->zi = 0;
    c->wr = (float)cos(-2 * M_PI * res / 160000); c->wi = (float)sin(-2 * M_PI * res / 160000);
    c->ar_ = calloc(LA + BLK, sizeof(float)); c->ai_ = calloc(LA + BLK, sizeof(float));
    c->na2 = LA - 1;
    c->br = calloc(BLK + LB, sizeof(float)); c->bi = calloc(BLK + LB, sizeof(float));
    c->nb = LB - 1;                              /* zero history */
    c->c = calloc(BLK + LC, sizeof(float)); c->nc = LC - 1;
    c->lr = 1; c->li = 0;
    firwin(c->h0, L0, 12000, rate);              /* A1MODE=3: pass +-8 kHz, alias-free to 152k */
    long o0 = lrint(c->off);
    g = rate; t = labs(o0);
    while (t) { long r = g % t; g = t; t = r; }
    if (rate / g > MAXP) {                       /* round to 1 kHz: period at most rate/1000 */
        o0 = lrint(c->off / 1000.0) * 1000;
        g = rate; t = labs(o0);
        while (t) { long r = g % t; g = t; t = r; }
    }
    c->lp0 = o0 ? (int)(rate / g) : 1;
    c->l0r = malloc(c->lp0 * sizeof(float)); c->l0i = malloc(c->lp0 * sizeof(float));
    for (int k = 0; k < c->lp0; k++) {
        c->l0r[k] = (float)cos(-2 * M_PI * (double)o0 * k / rate);
        c->l0i[k] = (float)sin(-2 * M_PI * (double)o0 * k / rate);
    }
    c->m0r = calloc(L0 + BLK, sizeof(float)); c->m0i = calloc(L0 + BLK, sizeof(float));
    c->nm0 = L0 - 1;
}

static void emit(void);
static int gated;                  /* -f: squelch-gated HamFLAC overs (flacout.cpp) */
void flac_tick(int ch, int nch, const int16_t *aud, int n, float level);   /* flacout.cpp */
void flac_finish(void);
void flac_note(const char *json, size_t n);                                /* flacout.cpp */

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

/* one channel-filtered sample at FS1: the level, then the discriminator, angle(x * conj(last)) */
static void disc(chan_t *c, float sr, float si)
{
    c->pacc += sr * sr + si * si;
    if (++c->pn == FS1 / 10) {
        c->level = (float)(10 * log10(c->pacc / c->pn + 1e-20));
        c->pacc = 0; c->pn = 0;
    }
    float pr = sr * c->lr + si * c->li, pi = si * c->lr - sr * c->li;
    c->lr = sr; c->li = si;
    stage_c(c, atan2f(pi, pr) * (FS1 / (2 * (float)M_PI)));
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
        disc(c, sr, si);
    }
    if (c->pb > 4096) {
        memmove(c->br, c->br + c->pb, (c->nb - c->pb) * sizeof(float));
        memmove(c->bi, c->bi + c->pb, (c->nb - c->pb) * sizeof(float));
        c->nb -= c->pb; c->pb = 0;
    }
}

static int a1mode = 3;             /* $A1MODE: 3 (default) mix from a table, then one 63-tap stage A
                                      straight to 160 kS/s, the fastest on the Pluto (114% of real
                                      time for 8 channels, 11-22); 0 the LO in a 9-tap triangle, 1 mix
                                      then boxcars (both slower there: 174-305%); 2 nothing (a floor) */

/* A1MODE=3: mix the block (n new samples after L1-1 of history) into the channel's buffer, then
 * a 63-tap stage A at RATE that keeps every qa*q1-th output, into stage B's input */
static void stage_a0(chan_t *c, const float *restrict xr_, const float *restrict xi_, int n)
{
    float *restrict mr = c->m0r + c->nm0, *restrict mi = c->m0i + c->nm0;
    const float *restrict x0r = xr_ + (L1 - 1), *restrict x0i = xi_ + (L1 - 1);
    for (int s0 = 0; s0 < n;) {                  /* runs inside one LO period: no modulo inside */
        int run = c->lp0 - c->lk0;
        if (run > n - s0)
            run = n - s0;
        const float *restrict lr = c->l0r + c->lk0, *restrict li = c->l0i + c->lk0;
        const float *restrict ar = x0r + s0, *restrict ai = x0i + s0;
        float *restrict orr = mr + s0, *restrict oi = mi + s0;
        for (int k = 0; k < run; k++) {
            orr[k] = ar[k] * lr[k] - ai[k] * li[k];
            oi[k] = ar[k] * li[k] + ai[k] * lr[k];
        }
        s0 += run;
        c->lk0 += run;
        if (c->lk0 == c->lp0)
            c->lk0 = 0;
    }
    c->nm0 += n;
    const float *restrict h = c->h0;
    const int qd = c->q1 * c->qa;
    while (c->pa0 + L0 <= c->nm0) {
        const float *restrict r = c->m0r + c->pa0, *restrict i = c->m0i + c->pa0;
        float sr = 0, si = 0;
        for (int k = 0; k < L0; k++) {
            sr += h[k] * r[k];
            si += h[k] * i[k];
        }
        c->br[c->nb] = sr;
        c->bi[c->nb] = si;
        c->nb++;
        c->pa0 += qd;
    }
    int keep = c->nm0 - c->pa0;                  /* compact: from the next window on */
    memmove(c->m0r, c->m0r + c->pa0, keep * sizeof(float));
    memmove(c->m0i, c->m0i + c->pa0, keep * sizeof(float));
    c->nm0 = keep; c->pa0 = 0;
    stage_b(c);
}

/* A1MODE=1: mix the block to 0 Hz from the LO table, then the triangle as two boxcars */
static int stage_a1_mix(chan_t *c, const float *restrict xr_, const float *restrict xi_, int n)
{
    float *restrict mr = c->mr, *restrict mi = c->mi;     /* [0, L1-1) history, then the block */
    const float *restrict x0r = xr_ + (L1 - 1), *restrict x0i = xi_ + (L1 - 1);
    for (int s0 = 0; s0 < n;) {                  /* runs inside one LO period: no modulo inside */
        int run = c->lp - c->lk;
        if (run > n - s0)
            run = n - s0;
        const float *restrict lr = c->lr_ + c->lk, *restrict li = c->li_ + c->lk;
        const float *restrict ar = x0r + s0, *restrict ai = x0i + s0;
        float *restrict orr = mr + (L1 - 1) + s0, *restrict oi = mi + (L1 - 1) + s0;
        for (int k = 0; k < run; k++) {
            orr[k] = ar[k] * lr[k] - ai[k] * li[k];
            oi[k] = ar[k] * li[k] + ai[k] * lr[k];
        }
        s0 += run;
        c->lk += run;
        if (c->lk == c->lp)
            c->lk = 0;
    }
    const int end = (L1 - 1) + n, ns = end - 4;
    float *restrict qr = c->sr, *restrict qi = c->si;
    for (int k = 0; k < ns; k++) {
        qr[k] = mr[k] + mr[k + 1] + mr[k + 2] + mr[k + 3] + mr[k + 4];
        qi[k] = mi[k] + mi[k + 1] + mi[k + 2] + mi[k + 3] + mi[k + 4];
    }
    float *restrict o1r = c->ar_, *restrict o1i = c->ai_;
    int n2 = c->na2, p = c->p1;
    while (p + L1 <= end) {
        o1r[n2] = (qr[p] + qr[p + 1] + qr[p + 2] + qr[p + 3] + qr[p + 4]) * (1.0f / 25);
        o1i[n2] = (qi[p] + qi[p + 1] + qi[p + 2] + qi[p + 3] + qi[p + 4]) * (1.0f / 25);
        n2++;
        p += c->q1;
    }
    memmove(mr, mr + n, (L1 - 1) * sizeof(float));
    memmove(mi, mi + n, (L1 - 1) * sizeof(float));
    c->p1 = p - n;
    return n2;
}

/* A1 over the shared block (xr/xi: L1-1 samples of history, then n new), then A2 and B */
static void stage_a(chan_t *c, const float *restrict xr_, const float *restrict xi_, int n)
{
    if (a1mode == 2)
        return;
    if (a1mode == 3) {
        stage_a0(c, xr_, xi_, n);
        return;
    }
    float *restrict o1r = c->ar_, *restrict o1i = c->ai_;
    int n2 = c->na2, p = c->p1, q = c->q;
    const int end = (L1 - 1) + n, nq = c->nq, q1 = c->q1;
    if (a1mode == 1) {
        n2 = stage_a1_mix(c, xr_, xi_, n);
        p = c->p1 + n;                           /* so that the lines below leave it as it is */
    } else
    while (p + L1 <= end) {
        const float *restrict tr = c->tr + q * L1, *restrict ti = c->ti + q * L1;
        const float *restrict r = xr_ + p, *restrict i = xi_ + p;
        float sr = 0, si = 0;
        for (int k = 0; k < L1; k++) {
            sr += tr[k] * r[k] - ti[k] * i[k];
            si += tr[k] * i[k] + ti[k] * r[k];
        }
        o1r[n2] = sr; o1i[n2] = si; n2++;
        p += q1;
        if (++q == nq)
            q = 0;
    }
    c->p1 = p - n;                               /* the block moves on by n */
    c->q = q;
    c->na2 = n2;
    int keep;
    const float *restrict h = c->ha;             /* A2: decimate qa */
    while (c->pa + LA <= c->na2) {
        const float *restrict r = c->ar_ + c->pa, *restrict i = c->ai_ + c->pa;
        float sr = 0, si = 0;
        for (int k = 0; k < LA; k++) {
            sr += h[k] * r[k];
            si += h[k] * i[k];
        }
        float zr = c->zr, zi = c->zi;            /* turn out the residual offset */
        c->br[c->nb] = sr * zr - si * zi;
        c->bi[c->nb] = sr * zi + si * zr;
        c->nb++;
        c->zr = zr * c->wr - zi * c->wi;
        c->zi = zr * c->wi + zi * c->wr;
        if (++c->zn == 1024) {                   /* keep |z| = 1 */
            float m = 1.0f / sqrtf(c->zr * c->zr + c->zi * c->zi);
            c->zr *= m; c->zi *= m; c->zn = 0;
        }
        c->pa += c->qa;
    }
    keep = c->na2 - c->pa;
    memmove(c->ar_, c->ar_ + c->pa, keep * sizeof(float));
    memmove(c->ai_, c->ai_ + c->pa, keep * sizeof(float));
    c->na2 = keep; c->pa = 0;
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

/* up to n samples (4 bytes each) from stdin; short only at the end of the input */
static size_t read_block(int16_t *raw, size_t n)
{
    size_t want = 4 * n, have = 0;
    while (have < want) {
        ssize_t r = read(0, (char *)raw + have, want - have);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0)
            break;
        have += (size_t)r;
    }
    return have / 4;
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

/* ---- -R: earshot's FPGA channelizer bins (RIQ1 over TCP, offload/PROTOCOL.md) instead of raw IQ ----
 * The Pluto's PL splits 25.6 MS/s into 512 bins of 50 kHz, each a 100 kS/s complex stream (stream ID =
 * bin + 1; bin k at LO + k * 50 kHz, k - 512 for k >= 256). Per channel: the bin nearest the target,
 * a mix by the rest of the offset (at most 25 kHz), then one polyphase FIR that resamples 100 kS/s to
 * FS1 (up 8, down 25) and is the channel filter as well; then the same discriminator, audio stage and
 * gate as above. A few MAC/s million a channel: the A9 has room for all 16. This is our own reader
 * written from the protocol text; no earshot code is linked. */
#define RQ_RATE 100000
#define RQ_UP 8
#define RQ_DN 25                     /* 100000 * 8 / 25 = FS1 */
#define RQ_PH 63                     /* taps a phase */
#define RQ_BIN 50000.0
#define RQ_MAXPAY (1 << 20)

typedef struct {
    double hz;                       /* the target */
    uint32_t sid;                    /* its stream */
    float hp[RQ_UP][RQ_PH];          /* the prototype by phase, newest input first */
    float *xr, *xi; long n0, nx;     /* mixed input held, its first index (absolute), how many */
    long m;                          /* the next output, absolute at FS1 */
    long next;                       /* the next input sample expected (the stream's first-sample) */
    double ph;                       /* the mix's phase (cycles) */
    long got;                        /* samples fed, zeros for gaps included */
} riq_t;
static riq_t rq[MAXCH];
static double rq_scale = 1.0 / 2048;

static void riq_init(riq_t *q, chan_t *c, double hz, double lo)
{
    memset(q, 0, sizeof *q);
    memset(c, 0, sizeof *c);
    q->hz = hz;
    long k = lrint((hz - lo) / RQ_BIN);
    q->sid = (uint32_t)(((k % 512) + 512) % 512) + 1;
    static float h[RQ_UP * RQ_PH];
    firwin(h, RQ_UP * RQ_PH, 7500, (double)RQ_RATE * RQ_UP);   /* as stage B: +-7.5 kHz */
    for (int p = 0; p < RQ_UP; p++)
        for (int j = 0; j < RQ_PH; j++)
            q->hp[p][j] = h[p + RQ_UP * j] * RQ_UP;
    q->xr = calloc(RQ_PH + RQ_RATE, sizeof(float)); q->xi = calloc(RQ_PH + RQ_RATE, sizeof(float));
    q->n0 = -(RQ_PH - 1); q->nx = RQ_PH - 1;      /* zero history */
    q->next = -1;
    c->hz = hz; c->qc = FS1 / FS2;
    firwin(c->hc, LC, 0.45 * FS2, FS1);
    c->c = calloc(BLK + LC, sizeof(float)); c->nc = LC - 1;
    c->lr = 1; c->li = 0;
}

/* n samples of the channel's stream (NULL: n zeros, a gap), centred on cen Hz */
static void riq_feed(riq_t *q, chan_t *c, const uint8_t *iq, long n, double cen)
{
    while (n > 0) {
        long room = RQ_RATE - (q->nx - (RQ_PH - 1));
        long run = n < room ? n : room;
        double f = (q->hz - cen) / RQ_RATE;       /* cycles a sample */
        float *xr = q->xr + q->nx, *xi = q->xi + q->nx;
        double ph = q->ph;
        float wr = (float)cos(-2 * M_PI * f), wi = (float)sin(-2 * M_PI * f);
        float zr = (float)cos(-2 * M_PI * ph), zi = (float)sin(-2 * M_PI * ph);
        for (long s = 0; s < run; s++) {
            float ir = 0, ii = 0;
            if (iq) {
                const uint8_t *b = iq + 4 * s;
                ir = (int16_t)(b[0] | b[1] << 8) * (float)rq_scale;
                ii = (int16_t)(b[2] | b[3] << 8) * (float)rq_scale;
            }
            xr[s] = ir * zr - ii * zi;
            xi[s] = ir * zi + ii * zr;
            float t = zr * wr - zi * wi;
            zi = zr * wi + zi * wr; zr = t;
        }
        q->ph = fmod(ph + f * run, 1.0);
        q->nx += run; q->got += run;
        if (iq)
            iq += 4 * run;
        n -= run;
        for (;;) {                                /* outputs whose newest input is here */
            long t = RQ_DN * q->m, ni = t / RQ_UP;
            int p = (int)(t % RQ_UP);
            if (ni >= q->n0 + q->nx)
                break;
            const float *h = q->hp[p], *r = q->xr + (ni - q->n0), *i = q->xi + (ni - q->n0);
            float sr = 0, si = 0;
            for (int j = 0; j < RQ_PH; j++) {
                sr += h[j] * r[-j];
                si += h[j] * i[-j];
            }
            disc(c, sr, si);
            q->m++;
        }
        long keep0 = (RQ_DN * q->m) / RQ_UP - (RQ_PH - 1);   /* the oldest input still wanted */
        long drop = keep0 - q->n0;
        if (drop > 0) {
            memmove(q->xr, q->xr + drop, (q->nx - drop) * sizeof(float));
            memmove(q->xi, q->xi + drop, (q->nx - drop) * sizeof(float));
            q->n0 += drop; q->nx -= drop;
        }
    }
}

static int read_full(int fd, void *buf, size_t n)
{
    size_t have = 0;
    while (have < n) {
        ssize_t r = read(fd, (char *)buf + have, n - have);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0)
            return 0;
        have += (size_t)r;
    }
    return 1;
}

static uint64_t le(const uint8_t *b, int n)
{
    uint64_t v = 0;
    for (int k = n - 1; k >= 0; k--)
        v = v << 8 | b[k];
    return v;
}

/* "-" is stdin; else HOST:PORT, retried for 15 s while the producer starts */
static int riq_open(const char *src)
{
    if (!strcmp(src, "-"))
        return 0;
    char host[64];
    const char *colon = strrchr(src, ':');
    if (!colon || colon - src >= (int)sizeof host)
        return -1;
    memcpy(host, src, colon - src); host[colon - src] = 0;
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons((uint16_t)atoi(colon + 1));
    if (inet_pton(AF_INET, host, &a.sin_addr) != 1)
        return -1;
    for (int tries = 0; tries < 30; tries++) {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd >= 0 && connect(fd, (struct sockaddr *)&a, sizeof a) == 0)
            return fd;
        if (fd >= 0)
            close(fd);
        usleep(500000);
    }
    return -1;
}

/* -R: the band's activity from earshot's PSD (type 2, float32 dB a bin, low to high, ~10 a second):
 * per bin the max, the mean, a floor that follows down at once and up 0.02 dB a frame, and the
 * frames more than 10 dB over that floor. One 'S' record of JSON at the end. */
#define PSD_MAX 1024
static int psd_n;
static long psd_frames;
static double psd_sum[PSD_MAX], psd_center, psd_rate;
static float psd_max[PSD_MAX], psd_floor[PSD_MAX];
static uint32_t psd_act[PSD_MAX];

static void psd_frame(const uint8_t *p, int n, double cen, double rate)
{
    if (n > PSD_MAX)
        return;
    if (psd_n != n) {
        psd_n = n; psd_frames = 0;
        for (int k = 0; k < n; k++) { psd_sum[k] = 0; psd_max[k] = -1e9f; psd_floor[k] = 1e9f; psd_act[k] = 0; }
    }
    psd_center = cen; psd_rate = rate;
    for (int k = 0; k < n; k++) {
        float v;
        memcpy(&v, p + 4 * k, 4);                  /* little endian, as the Pluto and hamlet are */
        if (!(v > -400 && v < 400))
            continue;
        psd_sum[k] += v;
        if (v > psd_max[k]) psd_max[k] = v;
        if (v < psd_floor[k]) psd_floor[k] = v; else psd_floor[k] += 0.02f;
        if (psd_frames > 50 && v > psd_floor[k] + 10)
            psd_act[k]++;
    }
    psd_frames++;
}

static void psd_note(void)
{
    if (!psd_n || !psd_frames)
        return;
    size_t cap = 64 + (size_t)psd_n * 40, len = 0;
    char *j = malloc(cap);
    len += snprintf(j + len, cap - len, "{\"psd_bins\":%d,\"frames\":%ld,\"center_hz\":%.0f,\"rate_hz\":%.0f,\"max_db\":[",
                    psd_n, psd_frames, psd_center, psd_rate);
    for (int k = 0; k < psd_n; k++)
        len += snprintf(j + len, cap - len, "%s%.1f", k ? "," : "", psd_max[k]);
    len += snprintf(j + len, cap - len, "],\"mean_db\":[");
    for (int k = 0; k < psd_n; k++)
        len += snprintf(j + len, cap - len, "%s%.1f", k ? "," : "", psd_sum[k] / psd_frames);
    len += snprintf(j + len, cap - len, "],\"active_frames\":[");
    for (int k = 0; k < psd_n; k++)
        len += snprintf(j + len, cap - len, "%s%u", k ? "," : "", psd_act[k]);
    len += snprintf(j + len, cap - len, "]}");
    flac_note(j, len);
    free(j);
}

static int riq_main(const char *src, double lo, int nargs, char **hzs, long nsamp)
{
    nch = nargs;
    for (int j = 0; j < nch; j++) {
        double hz = atof(hzs[j]);
        if (fabs(hz - lo) > 10e6) {
            fprintf(stderr, "pluto_chan: %.0f is more than 10 MHz from the LO\n", hz);
            return 2;
        }
        riq_init(&rq[j], &ch[j], hz, lo);
        fprintf(stderr, "pluto_chan: %.0f Hz from stream %u\n", hz, rq[j].sid);
    }
    int fd = riq_open(src);
    if (fd < 0) {
        fprintf(stderr, "pluto_chan: cannot open %s\n", src);
        return 1;
    }
    static uint8_t pay[RQ_MAXPAY];
    uint8_t hd[64];
    long nmsg = 0, ngap = 0;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    while (read_full(fd, hd, 64)) {
        if (memcmp(hd, "RIQ1", 4) || le(hd + 4, 2) != 1 || le(hd + 6, 2) != 64) {
            fprintf(stderr, "pluto_chan: not a RIQ1 header\n");
            return 1;
        }
        uint32_t nb = (uint32_t)le(hd + 52, 4);
        if (nb > RQ_MAXPAY) {
            fprintf(stderr, "pluto_chan: RIQ1 payload of %u bytes\n", nb);
            return 1;
        }
        if (!read_full(fd, pay, nb))
            break;
        nmsg++;
        unsigned type = (unsigned)le(hd + 8, 2), fmt = (unsigned)le(hd + 56, 2);
        if (type == 4 && fmt == 3 && nmsg < 4)
            fprintf(stderr, "pluto_chan: config %.*s\n", nb < 300 ? (int)nb : 300, (const char *)pay);
        if (type == 2 && fmt == 2) {
            psd_frame(pay, (int)(nb / 4), (double)(int64_t)le(hd + 44, 8), (double)le(hd + 40, 4));
            continue;
        }
        if (type != 1 || fmt != 1)
            continue;
        uint32_t sid = (uint32_t)le(hd + 20, 4);
        long first = (long)le(hd + 32, 8), n = nb / 4;
        uint32_t rate = (uint32_t)le(hd + 40, 4);
        unsigned bits = (unsigned)le(hd + 58, 2);
        double cen = (double)(int64_t)le(hd + 44, 8);
        if (rate != RQ_RATE)
            continue;
        if (bits >= 8 && bits <= 16)
            rq_scale = 1.0 / (1 << (bits - 1));
        for (int j = 0; j < nch; j++) {
            riq_t *q = &rq[j];
            if (q->sid != sid)
                continue;
            const uint8_t *p = pay;
            long m = n;
            if (q->next >= 0 && first > q->next) {        /* a gap: zeros, up to 1 s */
                long z = first - q->next;
                riq_feed(q, &ch[j], NULL, z < RQ_RATE ? z : RQ_RATE, cen);
                ngap++;
            } else if (q->next >= 0 && first < q->next) {  /* overlap (pre-roll): skip what we have */
                long skip = q->next - first;
                if (skip >= m)
                    continue;
                p += 4 * skip; m -= skip;
            }
            riq_feed(q, &ch[j], p, m, cen);
            q->next = first + n;
        }
        while (1) {
            int ok = 1;
            for (int j = 0; j < nch; j++)
                ok &= ch[j].na >= FS2 / 10;
            if (!ok)
                break;
            emit();
        }
        if (nsamp) {
            int all = 1;
            for (int j = 0; j < nch; j++)
                all &= rq[j].got >= nsamp;
            if (all)
                break;
        }
    }
    if (gated) {
        psd_note();
        flac_finish();
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double el = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;
    long least = rq[0].got;
    for (int j = 1; j < nch; j++)
        if (rq[j].got < least)
            least = rq[j].got;
    fprintf(stderr, "pluto_chan: RIQ1, %d channels, %ld messages, %ld gaps, %ld PSD frames, %.2f s of the slowest stream in %.2f s\n",
            nch, nmsg, ngap, psd_frames, least / (double)RQ_RATE, el);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "-f")) {
        gated = 1;
        argv++; argc--;
    }
    if (argc > 4 && !strcmp(argv[1], "-R")) {
        const char *ns = getenv("NSAMP");        /* stop once every stream gave this many samples */
        return riq_main(argv[2], atof(argv[3]), argc - 4, argv + 4, ns ? atol(ns) : 0);
    }
    if (argc < 4) {
        fprintf(stderr, "usage: %s [-f] LO_HZ RATE HZ1 [HZ2 ...]  (int16 IQ on stdin)\n"
                "       %s [-f] -R HOST:PORT|- LO_HZ HZ1 [HZ2 ...]  (earshot's RIQ1 bins)\n"
                "  -f: squelch-gated overs, each a HamFLAC stream (flacout.cpp says the format)\n", argv[0], argv[0]);
        return 2;
    }
    double lo = atof(argv[1]);
    int rate = atoi(argv[2]);
    if (rate % FSA) {
        fprintf(stderr, "pluto_chan: RATE must be a multiple of %d\n", FSA);
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
    const char *ns = getenv("NSAMP");           /* stop after this many samples (reading a device) */
    long nsamp = ns ? atol(ns) : 0;
    const char *am = getenv("A1MODE");
    if (am)
        a1mode = atoi(am);
    const char *te = getenv("THREADS");
    if (te)
        nthr = atoi(te);
    if (nthr < 1) nthr = 1;
    if (nthr > nch) nthr = nch;
    xr = calloc(L1 - 1 + BLK, sizeof(float));   /* L1-1 samples of history, then the block */
    xi = calloc(L1 - 1 + BLK, sizeof(float));
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
    /* read(2) in whole blocks, not stdio: from /dev/iio:deviceN each call is one DMA block copy */
    while ((got = read_block(raw, nsamp && nsamp - total < BLK ? (size_t)(nsamp - total) : BLK)) > 0) {
        for (size_t s = 0; s < got; s++) {
            xr[L1 - 1 + s] = raw[2 * s] * (1.0f / 2048);
            xi[L1 - 1 + s] = raw[2 * s + 1] * (1.0f / 2048);
        }
        nblk = (int)got;
        pthread_barrier_wait(&bar_go);           /* the workers run the channels */
        pthread_barrier_wait(&bar_done);
        memmove(xr, xr + got, (L1 - 1) * sizeof(float));   /* the history for the next block */
        memmove(xi, xi + got, (L1 - 1) * sizeof(float));
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
    fprintf(stderr, "pluto_chan: A1MODE %d, %d channels on %d threads, %.2f s of input in %.2f s (%.0f%% of real time)\n",
            a1mode, nch, nthr, total / (double)rate, el, 100 * el / (total / (double)rate));
    return 0;
}
