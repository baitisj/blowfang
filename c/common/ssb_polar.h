/* Polar SSB (envelope elimination and restoration, the Kahn method the uSDX uses), in portable
 * C for radios whose RF chip can only be retuned and power-modulated, not driven with I and Q:
 * the RT-950 Pro's BK4829 first (docs/rt950-plan.md, "SSB transmit: correcting the
 * architecture"). The SSB signal A(t) cos(wc t + phi(t)) is made from two things such a radio
 * can do: the envelope A(t) on the PA power and the instantaneous frequency d phi / dt on the
 * PLL.
 *
 * Two parts:
 *
 * sp_polar turns real audio, one sample at a time (8 kHz on the RT-950), into the envelope and
 * the instantaneous frequency of its analytic signal, up times faster than the audio. A complex
 * FIR does the band-pass (300-2700 Hz), the Hilbert transform and the interpolation at once:
 * its real taps are the band-pass, its imaginary taps the Hilbert transform of the same
 * band-pass (one windowed prototype, so both paths have exactly the same delay), and it runs
 * as a polyphase interpolator at fs x up. The polar split happens at that higher rate because
 * the envelope and phase of SSB are not band-limited: near an envelope null the phase turns by
 * up to half a cycle almost at once, and the radio's PLL and PA move between their updates in
 * a way that has nothing to do with the analytic signal. docs/rt950-polar-sim.md measures how
 * much that costs at each rate.
 *
 *     sp_polar p;  float env[4], hz[4];
 *     sp_polar_init(&p, 8000.0f, 4, 127, 300.0f, 2700.0f, 0);
 *     for each audio sample x:  sp_polar_step(&p, x, env, hz);
 *         4 x (env -> PA power after sp_level, hz -> PLL offset from the dial after sp_fq)
 *
 * The frequency is the RF offset from the suppressed carrier: positive in USB, negative in
 * LSB. Near nulls of the envelope the phase turns fast and the frequency spikes (and goes
 * negative). sp_polar_clamp can clamp it to [fmin, fmax] of the audio, as the uSDX does
 * (0-3000 Hz), either forgetting the phase it cut off (SP_CLAMP_PLAIN) or carrying it into the
 * next samples so that the phase catches up once the spike is over (SP_CLAMP_TRACK). The
 * default is no clamp (SP_CLAMP_NONE), because the simulation says a 0-3000 Hz clamp alone
 * holds the opposite sideband to about 20-27 dB down: the phase must follow the nulls. A
 * radio's PLL writes limit the spikes anyway (a write can turn the phase at most half a cycle,
 * so its mean frequency stays within about +-N/2 Hz of the audio's) without losing phase.
 *
 * The radio model (sp_fq, sp_level, sp_radio_run) says what a radio does with those two
 * streams: the PLL is written N times a second in steps of Q Hz (the BK4829's frequency
 * register is 10 Hz a unit) and holds each value until the next write; the PA has P power
 * levels, updated at its own rate; and the envelope may arrive D audio samples late (or early)
 * relative to the frequency. sp_radio_run rebuilds the complex baseband A_q exp(j phi_q) at a
 * higher rate (96 kHz) so that a host can measure the opposite sideband it lets back in
 * (c/common/ssb_polar_host.c, tests/test_ssb_polar.py, docs/rt950-polar-sim.md).
 *
 * sp_fq is also what a radio's own TX loop calls: one call per PLL write, given the mean
 * frequency wanted over that write's interval, it returns the register offset. In error-
 * feedback mode it keeps the rounding error and adds it to the next write, so the PLL's phase
 * (the integral of its frequency) tracks the wanted phase to within half a step-interval,
 * instead of drifting off in a random walk as plain rounding does.
 *
 * What the simulation says a radio needs for 30 dB of opposite-sideband suppression: about
 * 8000 PLL writes/s (10 Hz steps are fine), the PA updated 8000 times a second with 64 levels,
 * and the two paths matched to a tenth or a quarter of an 8 kHz sample; for 40 dB, 16000
 * writes/s, the PA at 16000-32000/s with 256 levels and the paths within 6 us
 * (docs/rt950-polar-sim.md).
 *
 * No malloc: the caller owns every state and buffer (sp_polar is about 9 KB at the default
 * SP_TAPS_MAX and SP_UP_MAX). sp_polar_step costs 2 x up x ntaps multiply-adds, an atan2f and
 * a sqrtf per output sample. Float throughout (the AT32F403A has an FPU); sp_radio_run keeps
 * the phase in double so that long simulations stay exact.
 * SPDX-License-Identifier: MIT */
#ifndef SSB_POLAR_H
#define SSB_POLAR_H

#include <stddef.h>
#include <stdint.h>

#ifndef SP_TAPS_MAX
#define SP_TAPS_MAX 127    /* FIR taps per audio sample (the length in audio samples) */
#endif
#ifndef SP_UP_MAX
#define SP_UP_MAX 8        /* most output samples per audio sample */
#endif

/* what sp_polar does with frequencies outside [fmin, fmax] */
#define SP_CLAMP_TRACK 0   /* clamp, and carry the clamped-off phase into the next samples */
#define SP_CLAMP_PLAIN 1   /* clamp, and forget it (the phase stays off by what was cut) */
#define SP_CLAMP_NONE 2    /* no clamp at all (for comparison) */

typedef struct {
    float hr[SP_UP_MAX * SP_TAPS_MAX];  /* band-pass taps (the real path), phase by phase */
    float hi[SP_UP_MAX * SP_TAPS_MAX];  /* Hilbert transform of the band-pass (imaginary path) */
    float buf[2 * SP_TAPS_MAX];         /* the input, twice, so that a window never wraps */
    unsigned ntaps;
    unsigned up;
    unsigned pos;
    float fs;                   /* the output rate, audio rate x up */
    float fmin;                 /* clamp range of the audio frequency, Hz */
    float fmax;
    int clamp;                  /* SP_CLAMP_* */
    int lsb;
    float pre;                  /* the previous analytic sample */
    float pim;
    float carry;                /* phase still owed by the clamp, in Hz x samples */
} sp_polar;

/* fs the audio rate; up 1..SP_UP_MAX output samples per audio sample; ntaps odd,
   3..SP_TAPS_MAX, the FIR's length in audio samples (it has up x (ntaps - 1) + 1 taps at the
   output rate); f_lo..f_hi the band-pass edges (-6 dB); lsb 0 for USB. Sets no clamp
   (SP_CLAMP_NONE, range 0..3000 Hz for a later mode change). Returns 0, or -1 if an argument
   is bad. */
int sp_polar_init(sp_polar *p, float fs, unsigned up, unsigned ntaps, float f_lo, float f_hi,
                  int lsb);

/* the clamp range and mode (fmin < fmax, both within +-fs x up / 2) */
void sp_polar_clamp(sp_polar *p, float fmin, float fmax, int mode);

/* one audio sample in; up envelopes and RF frequency offsets (Hz) out, at fs x up, delayed by
   (ntaps - 1) / 2 audio samples. Each frequency is the mean over the interval since the output
   sample before (the phase step from it), so the phase at output sample n is the sum of the
   frequencies up to n, over fs x up. */
void sp_polar_step(sp_polar *p, float x, float *env, float *hz);

/* the last analytic sample the last step made (real: the band-passed audio; imaginary: its
   Hilbert transform), for tests */
void sp_polar_analytic(const sp_polar *p, float *re, float *im);

/* ---- the radio model ---- */

/* how the PLL register is chosen from the wanted frequency */
#define SP_FQ_FEEDBACK 0   /* error feedback: the PLL phase tracks the wanted phase */
#define SP_FQ_ROUND 1      /* plain rounding of each write */

typedef struct {
    float step;            /* Q, Hz a register unit; 0 = no quantisation */
    int mode;              /* SP_FQ_* */
    float err;             /* the rounding error carried to the next write, in units */
} sp_fq;

void sp_fq_init(sp_fq *q, float step_hz, int mode);

/* one PLL write: hz is the mean frequency wanted over the interval this write holds for.
   Returns the register offset in units of step (round to nearest); with step 0 it returns 0
   and sp_fq_hz gives back hz itself. */
int32_t sp_fq_write(sp_fq *q, float hz);

/* the frequency the last write actually gives, Hz */
float sp_fq_hz(const sp_fq *q, int32_t reg, float hz_wanted);

/* the PA level for an envelope: round(env / full * (levels - 1)), clamped to 0..levels - 1 */
unsigned sp_level(float env, float full, unsigned levels);

typedef struct {
    float fs_in;           /* rate of the envelope and frequency streams (audio rate x up) */
    float fs_out;          /* rate of the rebuilt baseband (48000) */
    float writes;          /* N, PLL writes a second; 0 = the ideal continuous phase */
    float step;            /* Q, Hz a PLL unit; 0 = no frequency quantisation */
    int fq_mode;           /* SP_FQ_* */
    unsigned levels;       /* P, PA levels (0 = no amplitude quantisation) */
    float env_rate;        /* PA updates a second; 0 = the ideal continuous envelope */
    float full;            /* the envelope that maps to the top PA level */
    float delay;           /* D, input samples the envelope lags the frequency (may be < 0) */
} sp_radio;

/* defaults: 32 kHz in, 96 kHz out, 8000 writes/s of 10 Hz steps with error feedback, 256
   levels at 8000 updates/s, full scale 1, no delay */
void sp_radio_defaults(sp_radio *r);

/* Rebuilds what the radio transmits, as complex baseband around the dial frequency, from n
   samples of envelope and frequency (the output of sp_polar_step). Writes up to nmax samples
   to re[] and im[] and returns how many: n x fs_out / fs_in, less what nmax cuts.

   The two paths are lined up as a radio would at its best: the PLL phase is exact at each
   write instant and moves linearly between (each write holds the mean frequency of its
   interval), and each PA level holds for the update interval centred on the envelope sample it
   comes from. delay then shifts the envelope against that. Envelope samples between the
   input samples are linearly interpolated (for delay or env_rate off the input grid). */
size_t sp_radio_run(const sp_radio *r, const float *env, const float *hz, size_t n, float *re,
                    float *im, size_t nmax);

#endif
