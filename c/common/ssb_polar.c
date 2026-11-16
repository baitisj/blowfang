/* Polar SSB generator and radio model (ssb_polar.h). The analytic signal comes from one
 * complex FIR (a Kaiser-windowed low-pass prototype shifted up to the band's centre) run as a
 * polyphase interpolator; the envelope is its magnitude and the frequency its phase step per
 * output sample. sp_radio_run rebuilds
 * what a radio with a stepped PLL and a stepped PA sends. SPDX-License-Identifier: MIT */
#include "ssb_polar.h"

#include <math.h>
#include <string.h>

#define TWO_PI 6.283185307179586
#define KAISER_BETA 8.0 /* about 80 dB of stopband: below anything the radio model adds */

/* the zeroth-order modified Bessel function, by its series (only at init) */
static double bessel_i0(double x) {
    double sum = 1.0, term = 1.0, q = x * x / 4.0;
    int k;
    for (k = 1; k < 50; k++) {
        term *= q / ((double)k * (double)k);
        sum += term;
        if (term < sum * 1e-12)
            break;
    }
    return sum;
}

int sp_polar_init(sp_polar *p, float fs, unsigned up, unsigned ntaps, float f_lo, float f_hi,
                  int lsb) {
    unsigned k, r, total, m;
    double half, centre, rate, gain = 0.0;
    if (ntaps < 3 || ntaps > SP_TAPS_MAX || !(ntaps & 1u) || up < 1 || up > SP_UP_MAX ||
        fs <= 0.0f || f_lo < 0.0f || f_hi <= f_lo || f_hi >= fs / 2.0f)
        return -1;
    memset(p, 0, sizeof *p);
    p->ntaps = ntaps;
    p->up = up;
    p->fs = fs * (float)up;
    p->lsb = lsb;
    rate = (double)fs * (double)up;
    total = up * (ntaps - 1) + 1;
    m = (total - 1) / 2;
    /* The low-pass prototype passes +-half around 0 Hz; shifted to +centre it passes f_lo..f_hi
       and nothing at negative frequencies, nor the images of the audio rate. Its real part is
       the band-pass, its imaginary part the band-pass's Hilbert transform. Tap j of the long
       filter goes to phase j % up, position j / up; the gain is up (the interpolator's zeros
       between the audio samples) times 2 (the real path at unit gain). */
    half = (double)(f_hi - f_lo) / 2.0;
    centre = (double)(f_hi + f_lo) / 2.0;
    for (k = 0; k < total; k++) {
        double n = (double)k - (double)m;
        double x = 2.0 * half / rate * n;
        double sinc = n == 0.0 ? 1.0 : sin(TWO_PI / 2.0 * x) / (TWO_PI / 2.0 * x);
        double q = n / (double)m;
        double w = bessel_i0(KAISER_BETA * sqrt(1.0 - q * q)) / bessel_i0(KAISER_BETA);
        gain += sinc * w;
    }
    for (k = 0; k < total; k++) {
        double n = (double)k - (double)m;
        double x = 2.0 * half / rate * n;
        double sinc = n == 0.0 ? 1.0 : sin(TWO_PI / 2.0 * x) / (TWO_PI / 2.0 * x);
        double q = n / (double)m;
        double w = bessel_i0(KAISER_BETA * sqrt(1.0 - q * q)) / bessel_i0(KAISER_BETA);
        double a = TWO_PI * centre / rate * n;
        double h = 2.0 * (double)up * sinc * w / gain;
        r = k % up;
        p->hr[r * ntaps + k / up] = (float)(h * cos(a));
        p->hi[r * ntaps + k / up] = (float)(h * sin(a));
    }
    sp_polar_clamp(p, 0.0f, 3000.0f, SP_CLAMP_NONE);
    return 0;
}

void sp_polar_clamp(sp_polar *p, float fmin, float fmax, int mode) {
    p->fmin = fmin;
    p->fmax = fmax;
    p->clamp = mode;
    p->carry = 0.0f;
}

/* the polar split of one analytic sample */
static void split(sp_polar *p, float re, float im, float *env, float *hz) {
    float cr, ci, f;
    *env = sqrtf(re * re + im * im);
    /* the phase step from the previous analytic sample: arg(z conj(z_prev)), -pi..pi */
    cr = re * p->pre + im * p->pim;
    ci = im * p->pre - re * p->pim;
    f = atan2f(ci, cr) * p->fs / (float)TWO_PI;
    p->pre = re;
    p->pim = im;
    /* Near a null of the envelope the phase can turn by almost half a cycle in one sample:
       the frequency spikes towards +-fs/2 or goes negative, which a PLL cannot follow and
       which would put energy far outside the channel. Clamp it to fmin..fmax, as the uSDX
       does (it makes negative steps positive and caps the step). SP_CLAMP_TRACK also keeps
       what the clamp cut off (phase, as Hz x samples) and adds it to the next samples, so the
       phase catches up with the analytic signal's as soon as the frequency is back in range
       (while the envelope is still small). The debt is phase, so it is taken modulo one cycle
       (fs Hz x samples) the shorter way round. */
    if (p->clamp == SP_CLAMP_TRACK) {
        float want = f + p->carry, out = want;
        if (out < p->fmin)
            out = p->fmin;
        if (out > p->fmax)
            out = p->fmax;
        p->carry = want - out;
        if (p->carry > p->fs / 2.0f)
            p->carry -= p->fs;
        if (p->carry < -p->fs / 2.0f)
            p->carry += p->fs;
        f = out;
    } else if (p->clamp == SP_CLAMP_PLAIN) {
        if (f < p->fmin)
            f = p->fmin;
        if (f > p->fmax)
            f = p->fmax;
    }
    *hz = p->lsb ? -f : f;
}

void sp_polar_step(sp_polar *p, float x, float *env, float *hz) {
    unsigned k, r, n = p->ntaps;
    const float *b;
    p->pos = p->pos == 0 ? n - 1 : p->pos - 1;
    p->buf[p->pos] = x;
    p->buf[p->pos + n] = x;
    b = p->buf + p->pos;   /* b[k] is the input k audio samples ago */
    for (r = 0; r < p->up; r++) {
        const float *gr = p->hr + r * n, *gi = p->hi + r * n;
        float re = 0.0f, im = 0.0f;
        for (k = 0; k < n; k++) {
            re += gr[k] * b[k];
            im += gi[k] * b[k];
        }
        split(p, re, im, &env[r], &hz[r]);
    }
}

void sp_polar_analytic(const sp_polar *p, float *re, float *im) {
    *re = p->pre;
    *im = p->pim;
}

/* ---- the radio model ---- */

void sp_fq_init(sp_fq *q, float step_hz, int mode) {
    q->step = step_hz;
    q->mode = mode;
    q->err = 0.0f;
}

int32_t sp_fq_write(sp_fq *q, float hz) {
    float v;
    int32_t r;
    if (q->step <= 0.0f)
        return 0;
    v = hz / q->step;
    if (q->mode == SP_FQ_FEEDBACK)
        v += q->err;
    r = (int32_t)floorf(v + 0.5f);
    if (q->mode == SP_FQ_FEEDBACK)
        q->err = v - (float)r;
    return r;
}

float sp_fq_hz(const sp_fq *q, int32_t reg, float hz_wanted) {
    return q->step > 0.0f ? (float)reg * q->step : hz_wanted;
}

unsigned sp_level(float env, float full, unsigned levels) {
    float v;
    if (levels < 2 || full <= 0.0f || env <= 0.0f)
        return 0;
    v = env / full * (float)(levels - 1) + 0.5f;
    if (v >= (float)(levels - 1))
        return levels - 1;
    return (unsigned)v;
}

void sp_radio_defaults(sp_radio *r) {
    r->fs_in = 32000.0f;
    r->fs_out = 96000.0f;
    r->writes = 8000.0f;
    r->step = 10.0f;
    r->fq_mode = SP_FQ_FEEDBACK;
    r->levels = 256;
    r->env_rate = 8000.0f;
    r->full = 1.0f;
    r->delay = 0.0f;
}

/* the wanted phase (cycles) at a time in input samples, which only ever moves forward: the
   sum of the frequencies, linear between the samples (each frequency is the mean over the
   interval that ends at its sample) */
typedef struct {
    const float *hz;
    size_t n;
    size_t idx;
    double cyc;
    double fs;
} phase_cursor;

static double phase_at(phase_cursor *c, double x) {
    double f;
    while ((double)(c->idx + 1) <= x && c->idx + 1 < c->n) {
        c->idx++;
        c->cyc += (double)c->hz[c->idx] / c->fs;
    }
    f = c->idx + 1 < c->n ? (double)c->hz[c->idx + 1] : (double)c->hz[c->n - 1];
    return c->cyc + f * (x - (double)c->idx) / c->fs;
}

static float env_at(const float *env, size_t n, double x) {
    size_t i;
    double fr;
    if (x <= 0.0)
        return env[0];
    if (x >= (double)(n - 1))
        return env[n - 1];
    i = (size_t)x;
    fr = x - (double)i;
    return (float)((1.0 - fr) * (double)env[i] + fr * (double)env[i + 1]);
}

size_t sp_radio_run(const sp_radio *r, const float *env, const float *hz, size_t n, float *re,
                    float *im, size_t nmax) {
    phase_cursor pc;
    sp_fq q;
    size_t m, nout;
    double ratio = (double)r->fs_in / (double)r->fs_out;  /* input samples per output sample */
    double tw = r->writes > 0.0f ? 1.0 / (double)r->writes : 0.0;
    double t_k = 0.0, t_next = tw, ph_k = 0.0, want_k = 0.0, want_next, f_k = 0.0;
    long k = 0;
    if (n == 0)
        return 0;
    nout = (size_t)((double)n / ratio);
    if (nout > nmax)
        nout = nmax;
    pc.hz = hz;
    pc.n = n;
    pc.idx = 0;
    pc.cyc = 0.0;
    pc.fs = (double)r->fs_in;
    sp_fq_init(&q, r->step, r->fq_mode);
    if (tw > 0.0) {
        /* the first write: the mean frequency wanted over [0, tw) */
        want_next = phase_at(&pc, t_next * (double)r->fs_in);
        f_k = (double)sp_fq_hz(&q, sp_fq_write(&q, (float)((want_next - want_k) / tw)),
                               (float)((want_next - want_k) / tw));
        want_k = want_next;
    }
    for (m = 0; m < nout; m++) {
        double t = (double)m / (double)r->fs_out;   /* seconds */
        double ph, te, cyc;
        float a, s;
        if (tw > 0.0) {
            while (t >= t_next) {
                /* the next write: the PLL phase at its instant, then its frequency */
                ph_k += f_k * (t_next - t_k);
                k++;
                t_k = t_next;
                t_next = (double)(k + 1) * tw;
                want_next = phase_at(&pc, t_next * (double)r->fs_in);
                f_k = (double)sp_fq_hz(&q, sp_fq_write(&q, (float)((want_next - want_k) / tw)),
                                       (float)((want_next - want_k) / tw));
                want_k = want_next;
            }
            ph = ph_k + f_k * (t - t_k);
        } else {
            ph = phase_at(&pc, (double)m * ratio);
        }
        /* the envelope: delay input samples late; each PA level held for the update interval
           centred on the sample it was taken from */
        te = (double)m * ratio - (double)r->delay;
        if (r->env_rate > 0.0f) {
            double per = (double)r->fs_in / (double)r->env_rate;   /* input samples an update */
            te = floor(te / per + 0.5) * per;
        }
        a = env_at(env, n, te);
        if (r->levels >= 2)
            a = (float)sp_level(a, r->full, r->levels) * r->full / (float)(r->levels - 1);
        cyc = ph - floor(ph);
        s = (float)(TWO_PI * cyc);
        re[m] = a * cosf(s);
        im[m] = a * sinf(s);
    }
    return nout;
}
