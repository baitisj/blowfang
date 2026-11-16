/* Host front end for tone_fsk, for tests/test_tone_fsk.py: runs an FT8 or WSPR frame through
 * the PLL-step generator, rebuilds what the radio sends and measures it.
 *
 *   tone_fsk_host ft8|wspr [options]
 *
 *   --tones DIGITS     the symbols, one digit each (default: FT8 "CQ AG7EW CN87", WSPR
 *                      "AG7EW CN87 10", from tools/k6_ft8.py's encoders)
 *   --tone-file FILE   the symbols from a file (digits; anything else is skipped)
 *   --writes N         PLL writes a second (1000)
 *   --step Q           Hz a register unit (10)
 *   --dither none|sd1|sd2   plain rounding, first- or second-order error feedback (sd1)
 *   --trim HZ          a fine trim's unit (0: none)    --trim-max M   its range (+-M units)
 *   --base HZ          tone 0's offset from the dial (1500)
 *   --bt X             GFSK BT (FT8 2, WSPR 0)
 *   --fs F             rate of the rebuilt baseband (12000)
 *   --pad S            silence before and after the frame in --iq and --audio (0)
 *   --regs FILE        the writes, one a line: register offset, trim, Hz
 *   --iq FILE          the rebuilt complex baseband (exp(j phase), constant envelope) around
 *                      the dial, complex float32 interleaved
 *   --audio FILE       its real part, what a USB receiver at the dial hears, signed 16-bit
 *
 * It prints one line:
 *   mode=ft8 sym=79 writes=1000 step=10 dither=sd1 trim=0 nwrites=12640 err_max_hz=..
 *   err_rms_hz=.. spur_db=.. spur_hz=.. oob_db=.. dith_db=.. dith_hz=..
 * err: each symbol's mean frequency as sent, less the ideal waveform's mean over the same
 * symbol (the GFSK-shaped one for FT8), Hz. spur_db: the strongest spectral bin more than
 * 25 Hz outside the tones (tone 0 to the top tone) relative to the strongest inside, dB;
 * spur_hz: where it is (Hz from the dial). oob_db: all power outside over all power inside.
 * These include the modulation's own skirts (WSPR's plain FSK falls off slowly). dith_db: what
 * the stepping alone adds: the strongest bin of the difference between the sent baseband and
 * the ideal one (the exact waveform, unquantised), relative to the strongest bin of the sent
 * one, dB; dith_hz: where. Without error feedback the phase walks off and this nears 0 dB.
 * Spectra: Welch over the frame, 8192-point Blackman-Harris (1.46 Hz bins at 12 kHz).
 * SPDX-License-Identifier: MIT */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_fft.h"
#include "tone_fsk.h"

#define NFFT 8192u
#define GUARD_HZ 25.0
#define MAX_SYM 1024

/* tools/k6_ft8.py txhdr "CQ AG7EW CN87" and wsprhdr "AG7EW CN87 10" (as in
   c/k6/mod/ft8tx_msg.h and wsprtx_msg.h) */
static const char FT8_DEFAULT[] =
    "3140652000000001061640451703224431013140652274305303137565642235171265233140652";
static const char WSPR_DEFAULT[] =
    "130202223202333002322321331002222010032320222230110211232023301020011030121232210"
    "010310003101032223202023021023312112233210021310020032122332022200330101302011202";

static unsigned parse_tones(const char *s, uint8_t *t) {
    unsigned n = 0;
    for (; *s && n < MAX_SYM; s++)
        if (*s >= '0' && *s <= '9')
            t[n++] = (uint8_t)(*s - '0');
    return n;
}

static char *read_text(const char *path) {
    FILE *f = fopen(path, "rb");
    char *b;
    size_t n;
    if (!f) {
        perror(path);
        exit(1);
    }
    b = malloc(65536);
    n = fread(b, 1, 65535, f);
    b[n] = 0;
    fclose(f);
    return b;
}

static double db(double x) {
    return 10.0 * log10(x > 1e-30 ? x : 1e-30);
}

int main(int argc, char **argv) {
    tf_cfg c;
    tf_state s;
    uint8_t tones[MAX_SYM];
    unsigned nsym, j, ntone = 0;
    const char *mode, *tone_src, *regs_path = NULL, *iq_path = NULL, *audio_path = NULL;
    char *file_text = NULL;
    double fs = 12000.0, pad = 0.0, end, t_k, ph_k = 0.0, err_max = 0.0, err_sq = 0.0;
    double lo, hi, peak_in = 0.0, peak_out = 0.0, p_in = 0.0, p_out = 0.0, spur_hz = 0.0;
    double *psd, *sym_sum, ph_id = 0.0, peak_d = 0.0, dith_hz = 0.0;
    float *hzs, *yr, *yi, *er, *ei;
    uint32_t nw, k;
    size_t n, m, npad, i;
    int32_t reg, trim;
    float hz;
    FILE *rf = NULL;
    int a;

    if (argc < 2 || (strcmp(argv[1], "ft8") && strcmp(argv[1], "wspr"))) {
        fprintf(stderr, "usage: %s ft8|wspr [options] (see the comment at the top of "
                        "tone_fsk_host.c)\n", argv[0]);
        return 2;
    }
    mode = argv[1];
    if (!strcmp(mode, "ft8")) {
        tf_ft8_defaults(&c);
        tone_src = FT8_DEFAULT;
    } else {
        tf_wspr_defaults(&c);
        tone_src = WSPR_DEFAULT;
    }
    for (a = 2; a < argc; a++) {
        const char *o = argv[a];
        const char *v = a + 1 < argc ? argv[a + 1] : "";
        if (!strcmp(o, "--tones"))
            tone_src = v, a++;
        else if (!strcmp(o, "--tone-file"))
            tone_src = file_text = read_text(v), a++;
        else if (!strcmp(o, "--writes"))
            c.writes = (float)atof(v), a++;
        else if (!strcmp(o, "--step"))
            c.step_hz = (float)atof(v), a++;
        else if (!strcmp(o, "--dither")) {
            c.dither = !strcmp(v, "none") ? TF_ROUND : !strcmp(v, "sd2") ? TF_SD2 : TF_SD1;
            a++;
        } else if (!strcmp(o, "--trim"))
            c.trim_hz = (float)atof(v), a++;
        else if (!strcmp(o, "--trim-max"))
            c.trim_max = (int32_t)atoi(v), a++;
        else if (!strcmp(o, "--base"))
            c.base_hz = (float)atof(v), a++;
        else if (!strcmp(o, "--bt"))
            c.bt = (float)atof(v), a++;
        else if (!strcmp(o, "--fs"))
            fs = atof(v), a++;
        else if (!strcmp(o, "--pad"))
            pad = atof(v), a++;
        else if (!strcmp(o, "--regs"))
            regs_path = v, a++;
        else if (!strcmp(o, "--iq"))
            iq_path = v, a++;
        else if (!strcmp(o, "--audio"))
            audio_path = v, a++;
        else {
            fprintf(stderr, "tone_fsk_host: unknown option %s\n", o);
            return 2;
        }
    }
    nsym = parse_tones(tone_src, tones);
    for (j = 0; j < nsym; j++)
        if (tones[j] + 1u > ntone)
            ntone = tones[j] + 1u;
    if (tf_init(&s, &c, tones, nsym)) {
        fprintf(stderr, "tone_fsk_host: no symbols, or a bad --writes or --step\n");
        return 2;
    }

    /* the writes */
    nw = tf_writes(&s);
    hzs = malloc(sizeof(float) * nw);
    if (regs_path && !(rf = fopen(regs_path, "w"))) {
        perror(regs_path);
        return 1;
    }
    for (k = 0; tf_next(&s, &reg, &trim, &hz); k++) {
        hzs[k] = hz;
        if (rf)
            fprintf(rf, "%ld %ld %.4f\n", (long)reg, (long)trim, (double)hz);
    }
    if (rf)
        fclose(rf);

    /* each symbol's mean frequency as sent against the ideal waveform's */
    end = (double)nsym * (double)c.symbol_s;
    sym_sum = calloc(nsym, sizeof(double));
    for (k = 0; k < nw; k++) {
        double w0 = (double)k / (double)c.writes, w1 = (double)(k + 1) / (double)c.writes;
        if (w1 > end)
            w1 = end;
        while (w0 < w1) {
            unsigned js = (unsigned)(w0 / (double)c.symbol_s);
            double b = (double)(js + 1) * (double)c.symbol_s, seg = (b < w1 ? b : w1) - w0;
            if (js >= nsym)
                break;
            sym_sum[js] += seg * (double)hzs[k];
            w0 += seg;
            if (seg <= 0.0)
                break;
        }
    }
    for (j = 0; j < nsym; j++) {
        double t0 = (double)j * (double)c.symbol_s, t1 = t0 + (double)c.symbol_s;
        double e = sym_sum[j] / (double)c.symbol_s - (double)tf_mean_hz(&c, tones, nsym, t0, t1);
        if (fabs(e) > err_max)
            err_max = fabs(e);
        err_sq += e * e;
    }

    /* the baseband: the PLL's phase, exact between writes (each holds its frequency) */
    n = (size_t)(end * fs);
    npad = (size_t)(pad * fs);
    yr = calloc(n + 2 * npad, sizeof(float));
    yi = calloc(n + 2 * npad, sizeof(float));
    k = 0;
    t_k = 0.0;
    for (m = 0; m < n; m++) {
        double t = (double)m / fs, ph, cyc;
        while (k + 1 < nw && t >= (double)(k + 1) / (double)c.writes) {
            double t_next = (double)(k + 1) / (double)c.writes;
            ph_k += (double)hzs[k] * (t_next - t_k);
            t_k = t_next;
            k++;
        }
        ph = ph_k + (double)hzs[k] * (t - t_k);
        cyc = ph - floor(ph);
        yr[npad + m] = (float)cos(HF_TWO_PI * cyc);
        yi[npad + m] = (float)sin(HF_TWO_PI * cyc);
    }
    if (iq_path) {
        FILE *f = fopen(iq_path, "wb");
        if (!f) {
            perror(iq_path);
            return 1;
        }
        for (i = 0; i < n + 2 * npad; i++) {
            fwrite(&yr[i], sizeof(float), 1, f);
            fwrite(&yi[i], sizeof(float), 1, f);
        }
        fclose(f);
    }
    if (audio_path) {
        FILE *f = fopen(audio_path, "wb");
        if (!f) {
            perror(audio_path);
            return 1;
        }
        for (i = 0; i < n + 2 * npad; i++) {
            int16_t v = (int16_t)lrint(0.3 * 32767.0 * (double)yr[i]);
            fwrite(&v, sizeof v, 1, f);
        }
        fclose(f);
    }

    /* the stepping's own error: the sent baseband less the ideal one */
    er = malloc(sizeof(float) * (n + 1));
    ei = malloc(sizeof(float) * (n + 1));
    for (m = 0; m < n; m++) {
        double t0 = (double)m / fs, cyc = ph_id - floor(ph_id);
        er[m] = yr[npad + m] - (float)cos(HF_TWO_PI * cyc);
        ei[m] = yi[npad + m] - (float)sin(HF_TWO_PI * cyc);
        ph_id += (double)tf_mean_hz(&c, tones, nsym, t0, t0 + 1.0 / fs) / fs;
    }

    /* the spectra over the frame */
    psd = malloc(sizeof(double) * NFFT);
    if (!hf_psd(er, ei, n, NFFT, psd)) {
        fprintf(stderr, "tone_fsk_host: frame too short to measure\n");
        return 1;
    }
    for (j = 0; j < NFFT; j++)
        if (psd[j] > peak_d) {
            peak_d = psd[j];
            dith_hz = hf_bin_hz(j, NFFT, fs);
        }
    if (!hf_psd(yr + npad, yi + npad, n, NFFT, psd)) {
        fprintf(stderr, "tone_fsk_host: frame too short to measure\n");
        return 1;
    }
    lo = (double)c.base_hz - GUARD_HZ;
    hi = (double)c.base_hz + (double)c.spacing_hz * (ntone ? ntone - 1 : 0) + GUARD_HZ;
    for (j = 0; j < NFFT; j++) {
        double f = hf_bin_hz(j, NFFT, fs);
        if (f >= lo && f <= hi) {
            p_in += psd[j];
            if (psd[j] > peak_in)
                peak_in = psd[j];
        } else {
            p_out += psd[j];
            if (psd[j] > peak_out) {
                peak_out = psd[j];
                spur_hz = f;
            }
        }
    }
    printf("mode=%s sym=%u writes=%g step=%g dither=%s trim=%g nwrites=%lu err_max_hz=%.4f "
           "err_rms_hz=%.4f spur_db=%.1f spur_hz=%.1f oob_db=%.1f dith_db=%.1f dith_hz=%.1f\n",
           mode, nsym, (double)c.writes, (double)c.step_hz,
           c.dither == TF_ROUND ? "none" : c.dither == TF_SD2 ? "sd2" : "sd1", (double)c.trim_hz,
           (unsigned long)nw, err_max, sqrt(err_sq / (nsym ? nsym : 1)),
           db(peak_out) - db(peak_in), spur_hz, db(p_out) - db(p_in),
           db(peak_d) - db(peak_in > peak_out ? peak_in : peak_out), dith_hz);
    free(hzs);
    free(sym_sum);
    free(yr);
    free(yi);
    free(er);
    free(ei);
    free(psd);
    free(file_text);
    return 0;
}
