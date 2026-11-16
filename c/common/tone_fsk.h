/* Constant-envelope single-tone digital modes (FT8, WSPR, and the like) sent as steps of a
 * radio's PLL, in portable C: the RT-950 Pro's first transmit milestone (docs/rt950-plan.md,
 * "SSB transmit: correcting the architecture"). In USB a single audio tone f is just a carrier
 * f above the dial, so an FSK mode needs no envelope and no audio path at all, only the PLL
 * moved by f, N times a second.
 *
 * The catch is resolution: the BK4829's frequency register is 10 Hz a unit, and FT8's tones are
 * 6.25 Hz apart, WSPR's 1.46 Hz. tf_next picks each write's register value by error feedback
 * (a first- or second-order sigma-delta between neighbouring register values), so that the
 * frequency averaged over a symbol is right to a small fraction of a step, though each write is
 * a whole number of steps. TF_ROUND (each write rounded on its own) is there for comparison.
 * An optional fine trim (trim_hz a unit, at most trim_max units either way: a crystal trim
 * register, say; whether the BK4829 has a usable one is unknown) takes the remainder after the
 * coarse step, with the same error feedback.
 *
 * The frequency wanted at each moment is FT8's GFSK (BT 2, WSJT-X's gen_ft8wave: each tone
 * step smoothed by 0.5 (1 + erf(pi sqrt(2 / ln 2) BT t / T)), the first and last tones held
 * beyond the frame) or plain FSK (bt 0, WSPR). Each write gets the exact mean of that
 * frequency over the interval it holds for, from the closed-form integral of erf, so the PLL's
 * phase follows the waveform WSJT-X would make.
 *
 *     tf_cfg c;  tf_state s;  int32_t reg, trim;  float hz;
 *     tf_ft8_defaults(&c);                       (or tf_wspr_defaults)
 *     tf_init(&s, &c, tones, 79);
 *     every 1 / c.writes s:  if (!tf_next(&s, &reg, &trim, &hz)) break;
 *                            PLL = dial + reg x c.step_hz  (+ trim x c.trim_hz)
 *
 * The register value is the offset from the dial (the suppressed carrier of USB), so tone 0
 * lands at dial + base_hz. No malloc: the tones stay the caller's. Float, except the time of
 * each write, kept in double so that a 110 s WSPR frame stays exact.
 * SPDX-License-Identifier: MIT */
#ifndef TONE_FSK_H
#define TONE_FSK_H

#include <stdint.h>

/* how each write's register value is chosen */
#define TF_ROUND 0   /* plain rounding of each write's mean frequency */
#define TF_SD1 1     /* first-order error feedback: the PLL phase tracks the wanted phase */
#define TF_SD2 2     /* second-order error feedback: less error at low offsets, more far out */

typedef struct {
    float base_hz;      /* offset of tone 0 from the dial, Hz */
    float spacing_hz;   /* between tones */
    float symbol_s;     /* symbol length */
    float bt;           /* GFSK bandwidth-time product; 0 = plain FSK */
    float writes;       /* N, PLL writes a second */
    float step_hz;      /* Q, Hz a register unit */
    float trim_hz;      /* the fine trim's unit, Hz; 0 = no trim */
    int32_t trim_max;   /* the trim's range, units either way */
    int dither;         /* TF_* */
} tf_cfg;

/* FT8: 8-FSK, 6.25 Hz, 160 ms, GFSK BT 2, tone 0 at 1500 Hz; 1000 writes/s of 10 Hz, TF_SD1 */
void tf_ft8_defaults(tf_cfg *c);

/* WSPR: 4-FSK, 12000 / 8192 Hz, 8192 / 12000 s, no shaping, tone 0 at 1500 Hz; 1000 writes/s
   of 10 Hz, TF_SD1 */
void tf_wspr_defaults(tf_cfg *c);

typedef struct {
    tf_cfg c;
    const uint8_t *tones;
    unsigned nsym;
    uint32_t k;          /* the next write */
    uint32_t nwrites;    /* writes in the frame */
    float q1;            /* the last two writes' quantisation errors (Hz, out - in) */
    float q2;
} tf_state;

/* Returns 0, or -1 if the configuration makes no sense (no symbols, no rate, no step). */
int tf_init(tf_state *s, const tf_cfg *c, const uint8_t *tones, unsigned nsym);

/* the writes the frame takes: ceil(nsym x symbol_s x writes) */
uint32_t tf_writes(const tf_state *s);

/* One PLL write: the register offset from the dial (units of step_hz), the trim (units of
   trim_hz; 0 without one) and the frequency they give (Hz, may be NULL). Returns 1, or 0
   after the last write of the frame. Write k holds from k / writes to (k + 1) / writes s
   after the frame starts (the last one to the frame's end). */
int tf_next(tf_state *s, int32_t *reg, int32_t *trim, float *hz);

/* the ideal mean frequency (Hz from the dial) over t0..t1 s after the frame starts */
float tf_mean_hz(const tf_cfg *c, const uint8_t *tones, unsigned nsym, double t0, double t1);

#endif
