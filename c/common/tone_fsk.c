/* Constant-envelope FSK by PLL steps (tone_fsk.h): the exact mean of the (GFSK-shaped)
 * frequency over each write's interval, then a register value by error feedback.
 * SPDX-License-Identifier: MIT */
#include "tone_fsk.h"

#include <math.h>
#include <string.h>

#define SQRT_PI 1.7724538509055160f
#define GFSK_K 5.336446f   /* pi sqrt(2 / ln 2): WSJT-X's GFSK constant */
#define ERF_EDGE 6.0f      /* erf is +-1 to float precision beyond this */

void tf_ft8_defaults(tf_cfg *c) {
    memset(c, 0, sizeof *c);
    c->base_hz = 1500.0f;
    c->spacing_hz = 6.25f;
    c->symbol_s = 0.16f;
    c->bt = 2.0f;
    c->writes = 1000.0f;
    c->step_hz = 10.0f;
    c->dither = TF_SD1;
}

void tf_wspr_defaults(tf_cfg *c) {
    memset(c, 0, sizeof *c);
    c->base_hz = 1500.0f;
    c->spacing_hz = 12000.0f / 8192.0f;
    c->symbol_s = 8192.0f / 12000.0f;
    c->bt = 0.0f;
    c->writes = 1000.0f;
    c->step_hz = 10.0f;
    c->dither = TF_SD1;
}

int tf_init(tf_state *s, const tf_cfg *c, const uint8_t *tones, unsigned nsym) {
    double n;
    if (!nsym || c->writes <= 0.0f || c->step_hz <= 0.0f || c->symbol_s <= 0.0f)
        return -1;
    memset(s, 0, sizeof *s);
    s->c = *c;
    s->tones = tones;
    s->nsym = nsym;
    n = (double)nsym * (double)c->symbol_s * (double)c->writes;
    s->nwrites = (uint32_t)ceil(n - 1e-6);
    return 0;
}

uint32_t tf_writes(const tf_state *s) {
    return s->nwrites;
}

/* The integral of one tone step's shape from -infinity to tau (s): the step is
   0.5 (1 + erf(a tau)), whose integral is 0.5 tau + 0.5 (tau erf(a tau) + exp(-(a tau)^2) /
   (a sqrt(pi))); a plain step (a = 0) integrates to max(tau, 0). */
static float step_integral(float a, float tau) {
    float x;
    if (a <= 0.0f)
        return tau > 0.0f ? tau : 0.0f;
    x = a * tau;
    if (x >= ERF_EDGE)
        return tau;
    if (x <= -ERF_EDGE)
        return 0.0f;
    return 0.5f * tau + 0.5f * (tau * erff(x) + expf(-x * x) / (a * SQRT_PI));
}

float tf_mean_hz(const tf_cfg *c, const uint8_t *tones, unsigned nsym, double t0, double t1) {
    double sym = (double)c->symbol_s, d = t1 - t0, reach;
    float a = c->bt > 0.0f ? GFSK_K * c->bt / c->symbol_s : 0.0f;
    float acc;
    long jlo, jhi, j;
    if (d <= 1e-9)
        d = 1e-9;
    /* The frequency is tone 0 plus each tone step k - 1 -> k, shaped and centred on the
       boundary k x symbol_s. Steps that finished before t0 count whole; steps that start after
       t1 not at all; the rest by the mean of their shape over t0..t1. */
    reach = a > 0.0f ? (double)(ERF_EDGE / a) : 0.0;
    jlo = (long)floor((t0 - reach) / sym) + 1;
    jhi = (long)ceil((t1 + reach) / sym) - 1;
    if (jlo < 1)
        jlo = 1;
    if (jhi > (long)nsym - 1)
        jhi = (long)nsym - 1;
    acc = (float)tones[jlo - 1 < (long)nsym ? jlo - 1 : (long)nsym - 1];
    for (j = jlo; j <= jhi; j++) {
        double b = (double)j * sym;
        float frac = (step_integral(a, (float)(t1 - b)) - step_integral(a, (float)(t0 - b))) /
                     (float)d;
        acc += ((float)tones[j] - (float)tones[j - 1]) * frac;
    }
    return c->base_hz + c->spacing_hz * acc;
}

int tf_next(tf_state *s, int32_t *reg, int32_t *trim, float *hz) {
    const tf_cfg *c = &s->c;
    double t0, t1, end;
    float x, v, out, q;
    int32_t r, t = 0;
    if (s->k >= s->nwrites)
        return 0;
    t0 = (double)s->k / (double)c->writes;
    t1 = (double)(s->k + 1) / (double)c->writes;
    end = (double)s->nsym * (double)c->symbol_s;
    if (t1 > end)
        t1 = end;
    x = tf_mean_hz(c, s->tones, s->nsym, t0, t1);
    /* error feedback: q is what the last writes' rounding added (out - in); subtracting it
       (first order) or its second difference (second order) from the next input keeps the sum
       of the writes, and so the PLL's phase, on the wanted one */
    v = x;
    if (c->dither == TF_SD1)
        v = x - s->q1;
    else if (c->dither == TF_SD2)
        v = x - 2.0f * s->q1 + s->q2;
    r = (int32_t)floorf(v / c->step_hz + 0.5f);
    out = (float)r * c->step_hz;
    if (c->trim_hz > 0.0f) {
        t = (int32_t)floorf((v - out) / c->trim_hz + 0.5f);
        if (t > c->trim_max)
            t = c->trim_max;
        if (t < -c->trim_max)
            t = -c->trim_max;
        out += (float)t * c->trim_hz;
    }
    q = out - v;
    if (c->dither != TF_ROUND) {
        s->q2 = s->q1;
        s->q1 = q;
    }
    s->k++;
    *reg = r;
    *trim = t;
    if (hz)
        *hz = out;
    return 1;
}
