/* A small FFT and a Welch power spectrum for the host programs that measure spectra
 * (ssb_polar_host.c, tone_fsk_host.c). Host only: it allocates its work buffers. Header only,
 * all static, so each program that includes it gets its own copy.
 * SPDX-License-Identifier: MIT */
#ifndef HOST_FFT_H
#define HOST_FFT_H

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define HF_TWO_PI 6.283185307179586

/* in-place radix-2 complex FFT, n a power of two */
static void hf_fft(double *re, double *im, unsigned n) {
    unsigned i, j, len;
    for (i = 1, j = 0; i < n; i++) {
        unsigned bit = n >> 1;
        for (; j & bit; bit >>= 1)
            j ^= bit;
        j ^= bit;
        if (i < j) {
            double t = re[i];
            re[i] = re[j];
            re[j] = t;
            t = im[i];
            im[i] = im[j];
            im[j] = t;
        }
    }
    for (len = 2; len <= n; len <<= 1) {
        double a = -HF_TWO_PI / (double)len;
        double wr = cos(a), wi = sin(a);
        for (i = 0; i < n; i += len) {
            double cr = 1.0, ci = 0.0;
            for (j = 0; j < len / 2; j++) {
                unsigned p = i + j, q = i + j + len / 2;
                double tr = re[q] * cr - im[q] * ci;
                double ti = re[q] * ci + im[q] * cr;
                double t;
                re[q] = re[p] - tr;
                im[q] = im[p] - ti;
                re[p] += tr;
                im[p] += ti;
                t = cr * wr - ci * wi;
                ci = cr * wi + ci * wr;
                cr = t;
            }
        }
    }
}

/* the signed frequency of FFT bin k */
static double hf_bin_hz(unsigned k, unsigned nfft, double fs) {
    return (k < nfft / 2 ? (double)k : (double)k - (double)nfft) * fs / (double)nfft;
}

/* Welch power spectrum of complex x (xi may be NULL for real x): segments of nfft (a power of
   two) with 4-term Blackman-Harris windows (sidelobes 92 dB down), half overlapped, averaged.
   psd[k] is bin k in FFT order (hf_bin_hz). Returns the number of segments (0 if n < nfft). */
static unsigned hf_psd(const float *xr, const float *xi, size_t n, unsigned nfft, double *psd) {
    double *w = malloc(sizeof(double) * nfft);
    double *re = malloc(sizeof(double) * nfft);
    double *im = malloc(sizeof(double) * nfft);
    unsigned k, segs = 0;
    size_t s;
    memset(psd, 0, sizeof(double) * nfft);
    for (k = 0; k < nfft; k++) {
        double a = HF_TWO_PI * (double)k / (double)nfft;
        w[k] = 0.35875 - 0.48829 * cos(a) + 0.14128 * cos(2.0 * a) - 0.01168 * cos(3.0 * a);
    }
    for (s = 0; s + nfft <= n; s += nfft / 2) {
        for (k = 0; k < nfft; k++) {
            re[k] = (double)xr[s + k] * w[k];
            im[k] = xi ? (double)xi[s + k] * w[k] : 0.0;
        }
        hf_fft(re, im, nfft);
        for (k = 0; k < nfft; k++)
            psd[k] += re[k] * re[k] + im[k] * im[k];
        segs++;
    }
    if (segs)
        for (k = 0; k < nfft; k++)
            psd[k] /= (double)segs;
    free(w);
    free(re);
    free(im);
    return segs;
}

#endif
