/* Host front end for ssb_polar, for tests/test_ssb_polar.py and tools/ssb_polar_sweep.py: runs
 * 8 kHz audio through the polar split and the radio model and measures what comes out.
 *
 *   ssb_polar_host [input] [radio] [--out FILE.cf32]
 *
 * input (one of; the default is the two-tone test, 700 + 1900 Hz):
 *   --tone HZ          a tone (repeat for more; equal amplitudes)
 *   --in FILE          8 kHz mono audio: signed 16-bit raw, or a 16-bit PCM .wav
 *   --speech           a synthetic speech-like signal (pitch pulses through moving formants,
 *                      syllables and pauses, some hiss)
 *   --secs S           length of tones or --speech (2 s)
 *   --seed N           --speech's random seed
 * polar split:
 *   --taps T           FIR length in audio samples (127)
 *   --up L             polar split at 8 kHz x L (4: 32 kHz)
 *   --lsb              lower sideband
 *   --clamp none|track|plain    clamp the frequency (none)
 *   --fmin HZ --fmax HZ         the clamp's range (0, 3000)
 * radio:
 *   --writes N         PLL writes a second (8000; 0 = ideal continuous phase)
 *   --step Q           Hz a PLL unit (10; 0 = exact)
 *   --round            plain rounding of each write (default: error feedback)
 *   --levels P         PA levels (256; 0 = exact)
 *   --env-rate R       PA updates a second (8000; 0 = ideal)
 *   --delay D          8 kHz audio samples the envelope lags the frequency (0; fractional,
 *                      negative allowed)
 *   --fs-out F         rate of the rebuilt baseband (96000)
 *   --out FILE         the rebuilt baseband, complex float32 interleaved
 *
 * It prints one line:
 *   sup_db=.. spur_db=.. oob_db=.. writes=.. step=.. levels=.. delay=.. env_rate=.. ...
 * sup_db: power in the wanted sideband (300-2700 Hz on the wanted side of the carrier) over
 * power in the same band on the opposite side, dB. spur_db: the strongest spectral bin
 * outside the wanted side's 0-3100 Hz (and more than 100 Hz from the carrier on the opposite
 * side), relative to the strongest bin in the wanted band, dB (negative). oob_db: all power
 * beyond +-3000 Hz over the wanted band's, dB (negative). Spectra: Welch, 8192-point
 * Blackman-Harris at fs-out (11.7 Hz bins at 96 kHz).
 * SPDX-License-Identifier: MIT */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_fft.h"
#include "ssb_polar.h"

#define FS 8000.0f
#define MAX_TONES 8
#define NFFT 8192u

static uint32_t rng_state = 12345u;

static float rnd(void) {   /* uniform 0..1 */
    rng_state = rng_state * 1664525u + 1013904223u;
    return (float)(rng_state >> 8) / 16777216.0f;
}

/* formant targets (F1, F2, F3 in Hz) of a few vowels */
static const float VOWELS[][3] = {
    {730.0f, 1090.0f, 2440.0f},   /* a */
    {270.0f, 2290.0f, 3010.0f},   /* i */
    {300.0f, 870.0f, 2240.0f},    /* u */
    {530.0f, 1840.0f, 2480.0f},   /* e */
    {570.0f, 840.0f, 2410.0f},    /* o */
};
#define NVOWELS (sizeof VOWELS / sizeof VOWELS[0])

/* Synthetic speech-like audio: a pitch pulse train (band-limited, falling 6 dB an octave, the
   pitch wandering 95-150 Hz) through three formant resonators that glide between vowels, cut
   into syllables of 120-300 ms with raised-cosine envelopes and pauses, plus bursts of hiss
   (fricatives). It has what matters here: a high crest factor and envelope nulls. */
static void speech(float *x, size_t n) {
    double ph = 0.0, y1[3] = {0}, y2[3] = {0};
    float cur[3], tgt[3];
    size_t i = 0;
    unsigned v = 0, k;
    for (k = 0; k < 3; k++)
        cur[k] = tgt[k] = VOWELS[0][k];
    while (i < n) {
        size_t len = (size_t)(FS * (0.12f + 0.18f * rnd()));
        size_t gap = rnd() < 0.3f ? (size_t)(FS * (0.05f + 0.25f * rnd())) : 0;
        int hiss = rnd() < 0.25f;
        float amp = 0.4f + 0.6f * rnd();
        size_t j;
        v = (v + 1u + (unsigned)(rnd() * (float)(NVOWELS - 1))) % NVOWELS;
        for (k = 0; k < 3; k++)
            tgt[k] = VOWELS[v][k];
        for (j = 0; j < len && i < n; j++, i++) {
            double t = (double)i / FS;
            double f0 = 120.0 + 20.0 * sin(HF_TWO_PI * 0.7 * t) + 10.0 * sin(HF_TWO_PI * 2.3 * t);
            double src = 0.0, s;
            unsigned h, nh = (unsigned)(3500.0 / f0);
            float e = 0.5f - 0.5f * cosf((float)HF_TWO_PI * (float)j / (float)len);
            ph += f0 / FS;
            ph -= floor(ph);
            for (h = 1; h <= nh; h++)
                src += sin(HF_TWO_PI * h * ph) / h;
            s = src;
            for (k = 0; k < 3; k++) {
                /* a two-pole resonator at the formant, bandwidth 60, 90, 120 Hz, unit peak gain */
                double bw = 60.0 + 30.0 * k;
                double r = exp(-HF_TWO_PI / 2.0 * bw / FS);
                double c = 2.0 * r * cos(HF_TWO_PI * cur[k] / FS);
                double y = (1.0 - r) * s + c * y1[k] - r * r * y2[k];
                y2[k] = y1[k];
                y1[k] = y;
                s = y;
                cur[k] += 0.002f * (tgt[k] - cur[k]);   /* glide, about 60 ms */
            }
            x[i] = amp * e * (float)s;
            if (hiss && j > len / 2)
                x[i] += 0.05f * amp * e * (rnd() - 0.5f);
        }
        for (j = 0; j < gap && i < n; j++, i++)
            x[i] = 0.0005f * (rnd() - 0.5f);   /* a little room noise in the pauses */
    }
}

static float *read_audio(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    long size;
    uint8_t *raw;
    size_t off = 0, len, i;
    float *x;
    if (!f) {
        perror(path);
        exit(1);
    }
    fseek(f, 0, SEEK_END);
    size = ftell(f);
    fseek(f, 0, SEEK_SET);
    raw = malloc((size_t)size + 1);
    if (fread(raw, 1, (size_t)size, f) != (size_t)size) {
        fprintf(stderr, "%s: short read\n", path);
        exit(1);
    }
    fclose(f);
    len = (size_t)size;
    if (len >= 12 && !memcmp(raw, "RIFF", 4) && !memcmp(raw + 8, "WAVE", 4)) {
        /* walk the chunks: check fmt (PCM, mono, 16-bit, 8 kHz), find data */
        size_t p = 12;
        off = len = 0;
        while (p + 8 <= (size_t)size) {
            uint32_t cl = (uint32_t)raw[p + 4] | (uint32_t)raw[p + 5] << 8 |
                          (uint32_t)raw[p + 6] << 16 | (uint32_t)raw[p + 7] << 24;
            if (!memcmp(raw + p, "fmt ", 4)) {
                unsigned fmt = raw[p + 8] | raw[p + 9] << 8, ch = raw[p + 10] | raw[p + 11] << 8;
                unsigned rate = raw[p + 12] | raw[p + 13] << 8 | raw[p + 14] << 16;
                unsigned bits = raw[p + 22] | raw[p + 23] << 8;
                if (fmt != 1 || ch != 1 || bits != 16 || rate != 8000) {
                    fprintf(stderr, "%s: need 8 kHz mono 16-bit PCM\n", path);
                    exit(1);
                }
            } else if (!memcmp(raw + p, "data", 4)) {
                off = p + 8;
                len = cl;
                if (off + len > (size_t)size)
                    len = (size_t)size - off;
                break;
            }
            p += 8 + cl + (cl & 1u);
        }
    }
    *n = len / 2;
    x = malloc(sizeof(float) * (*n + 1));
    for (i = 0; i < *n; i++)
        x[i] = (float)(int16_t)(raw[off + 2 * i] | raw[off + 2 * i + 1] << 8) / 32768.0f;
    free(raw);
    return x;
}

static double band(const double *psd, double fs, double lo, double hi) {
    double s = 0.0;
    unsigned k;
    for (k = 0; k < NFFT; k++) {
        double f = hf_bin_hz(k, NFFT, fs);
        if (f >= lo && f <= hi)
            s += psd[k];
    }
    return s;
}

static double db(double x) {
    return 10.0 * log10(x > 1e-30 ? x : 1e-30);
}

int main(int argc, char **argv) {
    float tones[MAX_TONES];
    unsigned ntones = 0, taps = 127, up = 4, k;
    const char *in = NULL, *out = NULL;
    int lsb = 0, clamp = SP_CLAMP_NONE, use_speech = 0;
    float secs = 2.0f, fmin = 0.0f, fmax = 3000.0f;
    sp_radio r;
    sp_polar p;
    float *x, *env, *hz, *yr, *yi;
    size_t n, i, skip, nout, m0, m1;
    double *psd, fo, wanted, opp, outside, peak_in = 0.0, peak_out = 0.0, sgn;
    float full = 0.0f, xmax = 0.0f, delay;
    int a;

    sp_radio_defaults(&r);
    for (a = 1; a < argc; a++) {
        const char *s = argv[a];
        const char *v = a + 1 < argc ? argv[a + 1] : "";
        if (!strcmp(s, "--tone") && ntones < MAX_TONES)
            tones[ntones++] = (float)atof(v), a++;
        else if (!strcmp(s, "--in"))
            in = v, a++;
        else if (!strcmp(s, "--speech"))
            use_speech = 1;
        else if (!strcmp(s, "--secs"))
            secs = (float)atof(v), a++;
        else if (!strcmp(s, "--seed"))
            rng_state = (uint32_t)strtoul(v, NULL, 0), a++;
        else if (!strcmp(s, "--taps"))
            taps = (unsigned)atoi(v), a++;
        else if (!strcmp(s, "--up"))
            up = (unsigned)atoi(v), a++;
        else if (!strcmp(s, "--lsb"))
            lsb = 1;
        else if (!strcmp(s, "--clamp")) {
            clamp = !strcmp(v, "plain") ? SP_CLAMP_PLAIN : !strcmp(v, "track") ? SP_CLAMP_TRACK
                                                                               : SP_CLAMP_NONE;
            a++;
        } else if (!strcmp(s, "--fmin"))
            fmin = (float)atof(v), a++;
        else if (!strcmp(s, "--fmax"))
            fmax = (float)atof(v), a++;
        else if (!strcmp(s, "--writes"))
            r.writes = (float)atof(v), a++;
        else if (!strcmp(s, "--step"))
            r.step = (float)atof(v), a++;
        else if (!strcmp(s, "--round"))
            r.fq_mode = SP_FQ_ROUND;
        else if (!strcmp(s, "--levels"))
            r.levels = (unsigned)atoi(v), a++;
        else if (!strcmp(s, "--env-rate"))
            r.env_rate = (float)atof(v), a++;
        else if (!strcmp(s, "--delay"))
            r.delay = (float)atof(v), a++;
        else if (!strcmp(s, "--fs-out"))
            r.fs_out = (float)atof(v), a++;
        else if (!strcmp(s, "--out"))
            out = v, a++;
        else {
            fprintf(stderr, "ssb_polar_host: unknown option %s (see the comment at the top of "
                            "ssb_polar_host.c)\n", s);
            return 2;
        }
    }

    /* the input, peak 0.9 */
    if (in) {
        x = read_audio(in, &n);
    } else {
        n = (size_t)(secs * FS);
        x = calloc(n + 1, sizeof(float));
        if (use_speech) {
            speech(x, n);
        } else {
            if (!ntones) {
                tones[ntones++] = 700.0f;
                tones[ntones++] = 1900.0f;
            }
            for (i = 0; i < n; i++)
                for (k = 0; k < ntones; k++)
                    x[i] += sinf((float)(HF_TWO_PI * fmod((double)tones[k] * (double)i / FS, 1.0)));
        }
    }
    if (n < 4 * taps) {
        fprintf(stderr, "ssb_polar_host: input too short\n");
        return 1;
    }
    for (i = 0; i < n; i++)
        if (fabsf(x[i]) > xmax)
            xmax = fabsf(x[i]);
    for (i = 0; xmax > 0.0f && i < n; i++)
        x[i] *= 0.9f / xmax;

    /* the polar split; the first taps samples (the FIR filling) are dropped */
    if (sp_polar_init(&p, FS, up, taps, 300.0f, 2700.0f, lsb)) {
        fprintf(stderr, "ssb_polar_host: bad --taps or --up\n");
        return 2;
    }
    sp_polar_clamp(&p, fmin, fmax, clamp);
    env = malloc(sizeof(float) * n * up);
    hz = malloc(sizeof(float) * n * up);
    for (i = 0; i < n; i++)
        sp_polar_step(&p, x[i], &env[i * up], &hz[i * up]);
    skip = (size_t)taps * up;
    for (i = skip; i < n * up; i++)
        if (env[i] > full)
            full = env[i];
    r.full = full > 0.0f ? full : 1.0f;

    /* the radio: the streams are at 8 kHz x up, the delay was given in audio samples */
    r.fs_in = FS * (float)up;
    delay = r.delay;
    r.delay = delay * (float)up;
    nout = (size_t)((double)(n * up - skip) * r.fs_out / r.fs_in) + 1;
    yr = malloc(sizeof(float) * nout);
    yi = malloc(sizeof(float) * nout);
    nout = sp_radio_run(&r, env + skip, hz + skip, n * up - skip, yr, yi, nout);
    if (out) {
        FILE *f = fopen(out, "wb");
        if (!f) {
            perror(out);
            return 1;
        }
        for (i = 0; i < nout; i++) {
            fwrite(&yr[i], sizeof(float), 1, f);
            fwrite(&yi[i], sizeof(float), 1, f);
        }
        fclose(f);
    }

    /* the measurement, 20 ms in from either end */
    fo = (double)r.fs_out;
    m0 = (size_t)(0.02 * fo);
    m1 = nout > 2 * m0 ? nout - m0 : nout;
    psd = malloc(sizeof(double) * NFFT);
    if (!hf_psd(yr + m0, yi + m0, m1 - m0, NFFT, psd)) {
        fprintf(stderr, "ssb_polar_host: input too short to measure\n");
        return 1;
    }
    sgn = lsb ? -1.0 : 1.0;   /* the wanted side */
    wanted = sgn > 0 ? band(psd, fo, 300.0, 2700.0) : band(psd, fo, -2700.0, -300.0);
    opp = sgn > 0 ? band(psd, fo, -2700.0, -300.0) : band(psd, fo, 300.0, 2700.0);
    outside = band(psd, fo, -fo, -3000.0) + band(psd, fo, 3000.0, fo);
    for (k = 0; k < NFFT; k++) {
        double f = sgn * hf_bin_hz(k, NFFT, fo);   /* > 0 on the wanted side */
        if (f >= 300.0 && f <= 2700.0 && psd[k] > peak_in)
            peak_in = psd[k];
        if ((f < -100.0 || f > 3100.0) && psd[k] > peak_out)
            peak_out = psd[k];
    }
    printf("sup_db=%.1f spur_db=%.1f oob_db=%.1f writes=%g step=%g levels=%u delay=%g "
           "env_rate=%g taps=%u up=%u clamp=%s fmax=%g %s round=%d n=%lu\n",
           db(wanted) - db(opp), db(peak_out) - db(peak_in), db(outside) - db(wanted),
           (double)r.writes, (double)r.step, r.levels, (double)delay, (double)r.env_rate, taps, up,
           clamp == SP_CLAMP_TRACK ? "track" : clamp == SP_CLAMP_PLAIN ? "plain" : "none",
           (double)fmax, lsb ? "lsb" : "usb", r.fq_mode == SP_FQ_ROUND, (unsigned long)nout);
    free(x);
    free(env);
    free(hz);
    free(yr);
    free(yi);
    free(psd);
    return 0;
}
