# RT-950 transmit by PLL steps: polar SSB and FT8/WSPR, simulated

*2026-11-16. Host simulation only: nothing here has run on an RT-950 Pro, and nothing was
transmitted.* This answers [rt950-plan.md](rt950-plan.md)'s first step: what PLL write rate,
frequency resolution, PA level resolution and delay match the radio must deliver before
anyone touches it, for voice SSB by polar modulation and for the constant-envelope digital
modes that come first.

Code (MIT, portable, no malloc, float for the AT32F403A's FPU):

- `c/common/ssb_polar.{h,c}`: the analytic signal from 8 kHz audio (one complex FIR is the
  300-2700 Hz band-pass, its Hilbert transform and an interpolator to 8 kHz x up), the polar
  split into envelope and instantaneous frequency, and the radio model: PLL writes of Q Hz
  steps N times a second with error feedback (`sp_fq`), P PA levels (`sp_level`), and
  `sp_radio_run`, which rebuilds what such a radio sends at 96 kHz.
- `c/common/tone_fsk.{h,c}`: FT8 (GFSK BT 2) and WSPR as a sequence of PLL register values,
  with first- or second-order error feedback between neighbouring 10 Hz values, an optional
  fine trim, and plain rounding for comparison.
- `c/common/ssb_polar_host.c`, `tone_fsk_host.c`: host front ends (options at the top of each
  file); `tests/test_ssb_polar.py`, `tests/test_tone_fsk.py`; `tools/ssb_polar_sweep.py`
  makes the tables below.

```
make -C c/common
python3 -m pytest -q tests/test_ssb_polar.py tests/test_tone_fsk.py
python3 tools/ssb_polar_sweep.py --grid          # the tables below (speech from sotto's codec2)
c/common/ssb_polar_host --speech --writes 8000 --levels 64 --delay 0.25
c/common/tone_fsk_host ft8 --writes 1000 --audio ft8.raw --pad 1
```

## Voice SSB by polar modulation

### The model

Audio at 8 kHz goes through the complex FIR (127 audio samples long, Kaiser-windowed, about
80 dB of stopband) and comes out as the analytic signal at 32 kHz (up 4). Its magnitude is the
envelope A, its phase step per sample the instantaneous frequency f, positive in USB, negative
in LSB. The radio then does what a BK4829 with a power-modulated PA would:

- **Frequency path:** the PLL is written N times a second. Each write is the mean frequency
  wanted over its interval, rounded to Q Hz (the BK4829 frequency register: 10 Hz a unit) with
  error feedback, so the PLL's phase stays within half a step-interval of the wanted phase.
  The PLL holds each value until the next write (and is assumed to settle at once).
- **Envelope path:** P linear levels from zero to the envelope's peak, updated at their own
  rate, each held over its update interval.
- **Delay:** the envelope D audio samples (125 us each) late against the frequency. D = 0 is
  the best line-up (the PLL phase exact at each write instant, each PA level centred on its
  sample); in the radio a trim would find it.

`sp_radio_run` rebuilds `A_q exp(j phi_q)` at 96 kHz; the host measures, on a Welch spectrum,
the power in 300-2700 Hz on the wanted side over the power in the same band on the opposite
side (**suppression, dB**), plus the strongest spur and the out-of-band power. Signals: the
two-tone test (700 + 1900 Hz, equal amplitudes, so true envelope nulls every 0.83 ms), a
synthetic speech-like signal (pitch pulses through gliding formants, syllables, pauses,
hiss), and real speech (`hts1a.raw` from codec2, in the sotto submodule). A single tone has a
constant envelope and frequency: it comes out more than 100 dB clean with 16 PA levels, two
samples of delay or 1000 writes/s (and still about 60 dB when its frequency is off the 10 Hz
grid and the error feedback dithers).

### What it says

1. **The polar split must run faster than the audio.** The envelope and phase of SSB are not
   band-limited: at a null the phase turns half a cycle almost at once. Even a perfect radio
   updated at 8 kHz holds the opposite sideband to 27 dB (two-tone) and 29-33 dB (speech);
   every doubling of the rate buys about 12 dB. The library interpolates to 32 kHz (50-58 dB
   ceiling), and the radio's updates must be fast for the same reason. The FIR's length does
   not matter here: 63 taps do as well as 127.
2. **Don't clamp the frequency to the audio band.** The uSDX-style clamp of the instantaneous
   frequency to 0-3000 Hz holds the opposite sideband to about 20-27 dB, whether the clamped
   phase is dropped or paid back later. The phase must follow the nulls, so the PLL must swing
   well outside the audio band for a moment: with N writes/s each write turns the phase at most
   half a cycle, so excursions stay within about +-N/2 Hz (+-4 kHz at 8000 writes/s). `sp_polar`
   keeps the clamp as an option (`SP_CLAMP_PLAIN`, `SP_CLAMP_TRACK`) and defaults to none.
3. **PLL write rate is the first requirement.** From the frequency path alone: 1000-2000
   writes/s give 12-18 dB (useless), 4000 about 24, **8000 about 31 (two-tone) and 35-37
   (speech), 16000 about 40 and 47-49**.
4. **10 Hz steps are fine.** With error feedback the step barely matters: 1, 10 and 50 Hz agree
   within 0.4 dB at 8000 writes/s; only 250 Hz costs 2-6 dB. (Plain rounding is as good here
   for suppression, but lets the phase random-walk, which FT8 below shows matters elsewhere.)
5. **The PA must be updated at 8000/s or more, with 64 levels or more.** Zero-order hold at
   4000 updates/s gives about 20 dB; 8000/s about 36-39 dB; 16000/s 44-46. Linear levels: the
   two-tone is happy with 16 (its envelope is a rectified sine), speech needs 32 for 34 dB, 64
   for 36-40, 256 for 46-52. The hold also puts images of the envelope at multiples of the
   update rate (out-of-band power 17-21 dB under the wanted at 8000/s): the PA control line
   wants a low-pass, or faster updates.
6. **The delay match is the hardest.** Half a sample (62 us) of mismatch leaves 24-28 dB; a
   whole sample 17-19 dB. **30 dB needs the paths matched to about +-30 us (a quarter sample),
   40 dB to about +-12 us (a tenth), 45 dB to +-6 us.** Early and late are alike. Since the
   PLL's own settling delays the frequency path, the trim must cover it, and the PLL's
   response must be the same at every write.

### What the radio must deliver

| for | PLL writes/s | PLL step | PA updates/s | PA levels (linear) | delay match |
|---|---:|---:|---:|---:|---:|
| 30 dB, two-tone and speech | 8000 | 10 Hz (any to 50) | 8000 | 64 | +-0.1-0.25 sample (12-30 us) |
| 40 dB, two-tone and speech | 16000 | 10 Hz | 32000 (16000 for speech alone) | 256 | +-0.05 sample (6 us) |

The "whole radios" table below checks these together: the 30 dB recipe gives 31.1 dB on the
two-tone and 31-33 dB on speech (30-31.5 with a 0.1-sample mismatch); the 40 dB recipe 40.0
on the two-tone and 43-47 on speech. Each 8000 writes/s is a BK4829 frequency write (REG_38,
and REG_39 only when the high word changes, if it is laid out as the BK4819's) every 125 us over the bit-banged SPI, shared with
the second chip, plus a PA update: about what the AT32 can do with the sweep chip stopped (it
is stopped in TX anyway), and only if a frequency write needs no relock (see below). The 40 dB
radio is probably beyond the BK4829; 30 dB is roughly what the classic phasing exciters
managed, and enough to try voice on 70 cm.

### The sweeps

Opposite-sideband suppression in dB, from `tools/ssb_polar_sweep.py`. "Ideal" means
continuous and exact (no writes, no levels, no hold).

#### Polar rate and filter (ideal radio)

Opposite-sideband suppression, dB. The polar split at 8 kHz x up, the radio continuous and exact. This is the ceiling for everything below.

| | two-tone | speech (syn) | speech (hts1a) |
|---|---:|---:|---:|
| up 1 (8 kHz), 127 taps | 27.2 | 28.6 | 33.1 |
| up 2 (16 kHz), 127 taps | 37.6 | 43.7 | 45.7 |
| up 4 (32 kHz), 127 taps | 50.0 | 56.0 | 58.2 |
| up 8 (64 kHz), 127 taps | 62.0 | 67.9 | 70.2 |
| up 4 (32 kHz), 63 taps | 50.0 | 56.8 | 58.3 |

#### Frequency clamp (ideal radio)

The uSDX-style clamp of the instantaneous frequency. track: the clamped-off phase is paid back later; plain: it is lost.

| | two-tone | speech (syn) | speech (hts1a) |
|---|---:|---:|---:|
| none | 50.0 | 56.0 | 58.2 |
| plain 0..3000 Hz | 23.5 | 25.7 | 25.4 |
| track 0..3000 Hz | 11.1 | 21.4 | 19.4 |
| plain -1000..4000 Hz | 23.9 | 28.4 | 28.2 |
| track -1000..4000 Hz | 18.2 | 29.8 | 28.7 |
| track -3000..6000 Hz | 29.1 | 41.6 | 40.8 |
| track -8000..11000 Hz | 42.9 | 52.5 | 54.0 |

#### PLL write rate N (frequency path only)

10 Hz steps with error feedback; the envelope continuous and exact, no delay.

| | two-tone | speech (syn) | speech (hts1a) |
|---|---:|---:|---:|
| N = 1000 /s | 12.4 | 17.7 | 15.8 |
| N = 2000 /s | 11.5 | 17.7 | 17.7 |
| N = 4000 /s | 23.7 | 23.0 | 24.3 |
| N = 8000 /s | 31.0 | 34.9 | 36.7 |
| N = 16000 /s | 39.5 | 46.7 | 48.8 |
| N = 32000 /s | 50.0 | 56.0 | 58.2 |

#### PLL step Q at N = 8000 /s (frequency path only)

fb: error feedback (the PLL phase tracks the wanted phase); round: each write rounded on its own.

| | two-tone | speech (syn) | speech (hts1a) |
|---|---:|---:|---:|
| Q = 1 Hz, fb | 31.0 | 34.9 | 36.7 |
| Q = 10 Hz, fb | 31.0 | 34.9 | 36.7 |
| Q = 50 Hz, fb | 31.0 | 34.7 | 36.3 |
| Q = 100 Hz, fb | 31.0 | 34.2 | 35.2 |
| Q = 250 Hz, fb | 28.7 | 31.5 | 30.8 |
| Q = 1 Hz, round | 31.0 | 34.9 | 36.7 |
| Q = 10 Hz, round | 31.0 | 34.9 | 36.7 |
| Q = 50 Hz, round | 31.0 | 34.8 | 36.4 |
| Q = 100 Hz, round | 31.0 | 34.4 | 35.4 |
| Q = 250 Hz, round | 31.0 | 33.0 | 32.0 |

#### PA update rate (envelope path only)

Each PA level held for its update interval (zero-order hold), levels exact; the frequency path continuous.

| | two-tone | speech (syn) | speech (hts1a) |
|---|---:|---:|---:|
| 4000 /s | 18.3 | 20.1 | 22.7 |
| 8000 /s | 38.5 | 36.1 | 38.7 |
| 16000 /s | 44.4 | 44.7 | 45.9 |
| 32000 /s | 51.5 | 58.0 | 59.9 |
| continuous | 50.0 | 56.0 | 58.2 |

#### PA levels P (envelope path only)

Linear levels from 0 to the envelope's peak; continuous otherwise.

| | two-tone | speech (syn) | speech (hts1a) |
|---|---:|---:|---:|
| P = 16 | 48.4 | 30.8 | 27.9 |
| P = 32 | 50.3 | 34.6 | 33.6 |
| P = 64 | 50.8 | 36.1 | 39.8 |
| P = 128 | 49.8 | 40.6 | 46.5 |
| P = 256 | 50.2 | 45.9 | 51.8 |
| P = 1024 | 49.9 | 54.4 | 56.9 |

#### Delay mismatch D (8 kHz samples; 1 = 125 us)

The envelope late by D against the frequency; continuous and exact otherwise.

| | two-tone | speech (syn) | speech (hts1a) |
|---|---:|---:|---:|
| D = 0 (0 us) | 50.0 | 56.0 | 58.2 |
| D = 0.05 (6.25 us) | 48.2 | 44.7 | 45.5 |
| D = 0.1 (12.5 us) | 45.5 | 38.9 | 39.7 |
| D = 0.25 (31.25 us) | 37.3 | 30.9 | 31.7 |
| D = 0.5 (62.5 us) | 28.1 | 24.3 | 25.3 |
| D = 1 (125 us) | 17.5 | 17.1 | 18.5 |
| D = 2 (250 us) | 7.4 | 10.8 | 11.9 |

#### Whole radios

Everything quantised at once (10 Hz PLL steps with error feedback, linear PA levels, zero-order hold on both paths).

| | two-tone | speech (syn) | speech (hts1a) |
|---|---:|---:|---:|
| N 4000, Q 10, PA 4000/s x 64, D 0 | 16.6 | 17.6 | 19.5 |
| N 8000, Q 10, PA 8000/s x 64, D 0 | 31.1 | 31.2 | 33.2 |
| N 8000, Q 10, PA 8000/s x 64, D 0.1 | 29.0 | 30.1 | 31.5 |
| N 8000, Q 10, PA 8000/s x 256, D 0 | 30.7 | 33.1 | 35.0 |
| N 8000, Q 10, PA continuous x 64, D 0 | 31.2 | 32.5 | 35.0 |
| N 16000, Q 10, PA 16000/s x 128, D 0 | 39.1 | 38.4 | 41.4 |
| N 16000, Q 10, PA 16000/s x 256, D 0.05 | 39.0 | 41.1 | 43.2 |
| N 16000, Q 10, PA 32000/s x 256, D 0 | 40.0 | 43.5 | 47.1 |
| N 32000, Q 10, PA 32000/s x 256, D 0 | 51.2 | 45.9 | 51.1 |

#### The requested grid (Q = 10 Hz; PA at 8000 updates/s)

Q = 1 and 50 Hz agree with these within 0.3 dB everywhere in the grid (`--grid` prints all
144 rows).

| N | D | two-tone P16 | P64 | P256 | speech (hts1a) P16 | P64 | P256 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1000 | 0 | 13.0 | 12.8 | 12.9 | 15.7 | 16.1 | 16.1 |
| 1000 | 0.5 | 12.9 | 12.8 | 12.8 | 15.6 | 16.0 | 16.1 |
| 1000 | 1 | 12.6 | 12.6 | 12.6 | 15.5 | 16.0 | 16.0 |
| 1000 | 2 | 12.4 | 12.4 | 12.5 | 15.3 | 15.7 | 15.7 |
| 2000 | 0 | 12.1 | 12.0 | 11.9 | 17.4 | 17.9 | 18.0 |
| 2000 | 0.5 | 12.0 | 11.8 | 11.8 | 16.9 | 17.4 | 17.5 |
| 2000 | 1 | 11.6 | 11.5 | 11.5 | 16.1 | 16.5 | 16.5 |
| 2000 | 2 | 11.3 | 11.2 | 11.3 | 14.1 | 14.4 | 14.4 |
| 4000 | 0 | 23.1 | 23.2 | 23.1 | 22.2 | 24.2 | 24.4 |
| 4000 | 0.5 | 19.5 | 19.4 | 19.4 | 20.5 | 21.7 | 21.9 |
| 4000 | 1 | 14.6 | 14.5 | 14.5 | 17.4 | 18.0 | 18.1 |
| 4000 | 2 | 7.4 | 7.4 | 7.4 | 13.0 | 13.2 | 13.3 |
| 8000 | 0 | 29.6 | 31.1 | 30.7 | 25.9 | 33.2 | 35.0 |
| 8000 | 0.5 | 21.1 | 21.5 | 21.4 | 22.5 | 24.5 | 24.8 |
| 8000 | 1 | 15.4 | 15.6 | 15.5 | 18.0 | 18.6 | 18.7 |
| 8000 | 2 | 7.3 | 7.3 | 7.3 | 12.3 | 12.4 | 12.5 |

### What the model leaves out

The PLL is assumed to land on each new frequency at once and without a glitch; a real loop
filter smooths the frequency path (a delay and a low-pass the envelope does not share). The PA
is assumed linear from zero, with no AM-to-PM. Both are measurements (below), after which the
model can take the measured responses.

## FT8 and WSPR by PLL steps (the first milestone)

A single audio tone in USB is a carrier offset from the dial, so these need no envelope: only
the PLL, moved once per write. The trouble is resolution. FT8's tones are 6.25 Hz apart and
WSPR's 1.46 Hz, finer than the 10 Hz register. Plain rounding is off by up to 4 Hz and merges
tones (WSPR's four tones all round to 1500 Hz from a 1500 Hz base). `tf_next` gives each write
the exact mean of the wanted frequency over its interval (FT8's GFSK from the closed-form
integral of erf) and picks the register value by error feedback, so the frequency averaged
over each symbol is right.

Each symbol's mean frequency as sent, less the ideal waveform's, worst over the frame (Hz), and
the stepping's own spur level (the strongest bin of sent minus ideal, relative to the signal,
dB). 10 Hz steps, tone 0 at 1503.3 Hz (off the grid; at exactly 1500 Hz the WSPR errors happen
to vanish):

| writes/s | FT8 round | FT8 1st order | FT8 2nd order | WSPR round | WSPR 1st order | WSPR 2nd order |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 4.03 / +3 | 0.53 / -19 | 0.98 / -20 | 4.76 / +1 | 0.089 / -24 | 0.19 / -26 |
| 200 | 4.03 / 0 | 0.28 / -27 | 0.43 / -28 | 4.76 / +1 | 0.038 / -32 | 0.077 / -35 |
| 300 | 4.03 / +1 | 0.175 / -32 | 0.30 / -32 | 4.76 / +1 | 0.029 / -36 | 0.059 / -40 |
| 500 | 4.03 / +1 | 0.115 / -37 | 0.175 / -39 | 4.76 / +1 | 0.020 / -40 | 0.038 / -47 |
| 1000 | 4.03 / +1 | **0.053 / -43** | 0.081 / -48 | 4.76 / +1 | **0.011 / -47** | 0.019 / -55 |
| 2000 | 4.03 / 0 | 0.028 / -50 | 0.035 / -57 | 4.76 / +1 | 0.004 / -53 | 0.009 / -64 |
| 8000 | 4.03 / +1 | 0.008 / -62 | 0.010 / -74 | 4.76 / +1 | 0.001 / -65 | 0.002 / -82 |

(Rounding's "spur" near 0 dB means its phase walks away from the ideal waveform altogether.)

- **Required:** for 0.2 Hz per symbol at 10 Hz steps, FT8 needs about **300 writes/s** and
  WSPR about 100 (the error is about one step over the writes in a symbol). **1000 writes/s**
  (first order, the default) gives 0.05 Hz on FT8 and 0.01 Hz on WSPR with the stepping's spurs
  43-47 dB down, within a few hundred Hz of the signal; second order moves them further out and
  lower. A fine trim (1 Hz units, +-5) under the same error feedback gets FT8 to 0.006 Hz and
  its spurs to -63 dB; under plain rounding alone, to 0.33 Hz.
- **Decodes:** the frame sent this way decodes with the repo's receivers (the K6's FT8
  decoder via `sim/k6digi/ft8_wav.py`, `sim/k6digi/wsprref.py`) at -10 dB SNR, every symbol
  sits on its tone, and at -18 dB the dithered FT8 decodes as often as WSJT-X's ideal waveform
  (8 of 8 noise seeds) while the rounded one manages 2 of 8. Rounded WSPR does not decode at
  all.
- **Messages:** the defaults are "CQ AG7EW CN87" and "AG7EW CN87 10", so any test frame
  identifies.

## What to measure on Andrew's radio

All in TX at low power into a dummy load or on 70 cm with AG7EW ident, the IC-7100 (or an SDR
recording IQ) listening:

1. **Retune glitch.** Key a carrier and write the frequency (REG_38/39) by +-10 Hz, 1000 times a
   second, as the FT8 sender will. Record the carrier as IQ: is the phase continuous across a
   write, or is there a jump, a dropout or a burst of spurs? Then +-3 kHz steps at 8000/s, as
   voice would need.
2. **REG_30 relock.** Does a frequency write in TX take effect on its own, or only after a
   REG_30 toggle (the VCO/PLL enable sequence the K5 firmwares use to tune)? If a toggle is
   needed, check whether it drops the carrier; if it does, voice SSB by PLL writes is out and
   the FM modulation input (DC-coupled, if one exists) is the frequency path to try instead.
3. **Settle time per write.** The PLL's step response to a 10 Hz and a 3 kHz step (time to
   within 1 Hz, overshoot). FT8 at 1000 writes/s wants it well under 1 ms; voice at 8000/s
   under about 30 us and, above all, the same every time (it is part of the delay to match).
4. **Register resolution and range.** That one unit is 10 Hz on the BK4829 too, and how far a
   single low-word write can move the frequency without touching the high word.
5. **SPI time per write.** The bit-banged transaction's length, so the write rate is known
   to fit (8000/s leaves 125 us for a write, a PA update and the DSP).
6. **A fine trim.** Whether a crystal-offset register (the BK4819's frequency calibration word
   in the UV-K5 firmwares) moves the carrier in finer steps, live, in TX: `tf_cfg.trim_hz` and
   `trim_max` are ready for it.
7. **For voice:** the PA path's update rate, levels and linearity (open question 5 in the
   plan), then the delay trim by nulling the opposite sideband of a two-tone.
