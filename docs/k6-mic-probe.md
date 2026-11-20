# Can mic audio reach the K6's MCU?

Jeff asked on 2026-11-19 for a slim sotto 1200 encoder on the K6 (sotto's `docs/slim-encoder.md`)
and, once it fitted, chose "mic probe first": find out whether the microphone's audio can reach
the MCU with no hardware change, before anything is built on it. `docs/k6-fd6818-audio.md` had
the standing answer, "no": the mic goes to the FD6818's MICIN pin, and the chip hands the MCU
meters and decoder results, never samples.

This page is the probe: what was tried, what the radio said, and the one rule it needed.

## Short answer

- **The MCU has an audio input after all: the beep pin.** PA11, the voice prompts' PWM pin,
  shares a node with the FD6818's audio output. Run as a one-pin delta-sigma converter
  (`c/k6/mod/pa11adc.h`) it digitises whatever the chip puts on its AF pin, with no part added:
  the receiver's hiss reads 50 dB above the converter's own noise in 300-3000 Hz, and a 1 kHz
  tone sent to the K6 comes back at 39 to 41 dB SINAD.
- **The VOX pin is a dead end.** PA0 sits near 0 V in receive and shows neither a waveform nor
  a loudness.
- **The level is low so far**: a speaker a foot from the mic reads about 26 dB over the
  converter's floor. Speech at the mic is louder, and the converter has gain left to find.
- **The chip can put the mic on that pin.** With the mic ADC and the TX DSP switched on while
  the K6 receives (REG_30 bits 2 and 1; the PA off, nothing keyed), REG_47's AF output select 4
  carries the mic's audio, and PA11 reads it: a 1 kHz tone played at the mic comes back with its
  harmonics, and three seconds of speech come back as speech (`c/k6/mod/micrec.c`).
- **No flash was needed.** The probe sets the two bits over the chip's bus pins, under the rule
  API level 14 will give `fd_write` (below).

## The board

| MCU pin | Net | What it offers |
| --- | --- | --- |
| PA0 (ADC channel 0) | VOX_DET | Aura sets it up as "VOX mic level sense" and never reads it |
| PA11 (TIM1_CH4) | BEEP | PWM out through 4.7k / 3.9 nF / 4.7k / 22 nF / 22k / 1 uF to VOICE_IN, the speaker amplifier's input; the chip's AF output feeds VOICE_IN through 2.2k |
| PB1 | GP0 (CTCSS) | the chip's GP0 output: one digital line, its source picked by REG_34 (`docs/k6-i2s-probe.md`) |
| PB2 | amplifier enable | off = nothing sounds |

The mic itself reaches only the chip (MICIN, with the chip's own bias).

## Probe 1: no flash

Two modules on the image already on the radio (keyed main 305eb04, API level 13), uploaded
over the air by the Pluto on 440.850 MHz, read back from the serial log by
`tools/k6_micprobe.py`. The K6 keys nothing in either.

### voxcap: PA0 at 16 kHz while the speaker plays tones

`c/k6/mod/voxcap.c` samples PA0 with the ADC at 16 kHz (TIM17 as its clock) while TIM1 plays
square waves of 296 to 2000 Hz through the speaker amplifier: if the pin were a rectified,
lightly smoothed mic signal it would show the tone, or twice it.

Run micp1a, 2026-11-20 02:15 UTC:

| Capture | Mean (of 4,095) | Tone at f / 2f | |
| --- | --- | --- | --- |
| Q, no tone | 8.0 | | climbs from 0 to 15 |
| S, 1 kHz on for 64 ms | 22.6 | | one straight climb from 4 to 39, the same before, during and after the tone |
| a..f, steady tones | 29 to 39 | under 0.15 | level off at 39 |

The readings follow the sampling, not the sound: the node creeps up 30 mV as the ADC's sample
capacitor pumps it, and falls back when the sampling stops. It is a high-impedance node at
0 V. Nothing drives it in receive, so it carries no waveform and not even a loudness. (The
volume knob's position was not known for this run; the pin's rest level and its drift settle
the question without it.)

### afpin: PA11 as a converter on the AF output

The chip's AF pin reaches no ADC pin, but PA11 hangs on the same node behind 9.4k and two
small capacitors. Audio on the AF pin moves the 3.9 nF capacitor at PA11 by tens of
millivolts, far too little to cross a logic threshold by itself. `pa11adc.h` closes a loop
around the pin's input threshold instead, about 1.6 million times a second:

1. push the capacitor up a fixed step (the pin an output, high, for 6 CPU cycles),
2. let go and read the pin as an input,
3. if it read high, pull the capacitor down (an output, low, for 6 + 5 x hold cycles).

The share of "high" answers in each ninety turns follows the current the audio pushes into
the capacitor: a first-order delta-sigma converter whose comparator is the pin's Schmitt
input and whose integrator is a capacitor already on the board. The answers go through a
triangle-weighted count (a second-order CIC decimator), which gives a sample of 0 to 8,100 at
17.8 kHz with hold 3.

Step 1 is what makes it work. A plain loop (read, drive the opposite way) was tried too: it
reads exactly 0.500 with no noise at all, because driving the pin flips its Schmitt input and
the capacitor never leaves the dead band between the two thresholds. The fixed push leaves
the Schmitt input high every time, so every read is against the lower threshold.

Run micp1a, 2026-11-20 02:15 UTC (sample rate from the radio's own 100 us ticks):

| Capture | Mean | 300-3000 Hz rms (of full scale) | |
| --- | --- | --- | --- |
| m: AF output muted | 0.682 | 0.00017 | the converter's own noise |
| h: received audio, no signal | 0.683 | 0.0511 | +49.5 dB: the receiver's hiss, with the chip's 300 Hz-3 kHz shape |
| the plain loop, muted or not | 0.500 | 0.00000 | dead |

Run micp1b, 2026-11-20 02:27 UTC: the Pluto sends a 1 kHz tone at 2.65 kHz deviation on
440.850 MHz (-68 dBm at the K6), and the module captures it at four drive times:

| Drive time (hold) | Rest level | Sample rate | Tone, of full scale | SINAD, 250-3400 Hz |
| --- | --- | --- | --- | --- |
| 2 | 0.868 | 19.4 kHz | 0.092 | 40.7 dB |
| 3 | 0.682 | 17.8 kHz | 0.078 | 39.4 dB |
| 5 | 0.479 | 15.3 kHz | 0.062 | 32.0, 32.3 dB |
| 5, 180 turns a sample | 0.478 | 7.7 kHz | 0.059 | 32.9 dB |
| 8 | 0.332 | 12.6 kHz | 0.052 | 35.9 dB |

The SINAD takes in the whole chain (the Pluto's modulator, the chip's discriminator and
filters, the converter), so the converter is at least this good. Drive time 5 rests near one
half, where a first-order converter makes its strongest tones of its own, and it shows; 2 and
3 are the ones to use.

Two things the runs taught about the bench, not the radio:

- A module starts while the upload that carried it is still on the air: the Pluto's
  transmission runs on for about 8 s (repeats, then its Morse ID at 800 Hz, -68 dBm). The first
  run's "hiss" capture was partly that. A probe waits for the channel to go quiet first.
- The uploader waits for the K6's report, which comes only when the module returns. A tone
  sent while a module runs therefore follows a one-round upload that gives up, and the module
  waits for a carrier that stays (afpin: 20 s).

## API level 14: a module may switch the mic ADC

Probe 2 has to turn the chip's mic ADC on while it receives, which is REG_30 bit 2, and
`fd_write` refused REG_30 whole (it holds the RX/TX state). Level 14 (the Aura patch,
`mod_fd_write`) adds one exception, and no new call:

- a REG_30 write that changes only bits 0x0206 (bit 9 the AF DAC, bit 2 the mic ADC, bit 1 the
  TX DSP) is taken; any other change is still refused;
- the mic bit is refused while the PA is on (REG_36 bit 7), and `tx_pa(1)` clears it first:
  **a module can never put the mic on the air**;
- `tx_on`, `tx_off` and every retune write REG_30 whole, and the core's return to receive after
  the module does too, so nothing a module sets there outlives it.

## Probe 2: the mic ADC on, with no flash

Jeff chose (2026-11-19) not to flash for the probe: where `fd_write` refuses REG_30, the
module writes that one register over the chip's bus pins itself (PB3 SCN, PB5 SCK, PB6 SDA,
as Aura's driver does), under level 14's rule and no other: only bits 2 and 1 change, never
unless the chip is in receive with the PA off, and the register is read back
(`c/k6/mod/micadc.h`). On a level 14 image the same module goes through `fd_write`.

`c/k6/mod/micprobe.c` looks at everything the MCU can see in three states (0 as found, 1 the
mic ADC on, 2 the mic ADC and the TX DSP on), once with the room quiet and once with a 1 kHz
tone at the K6's mic. The tone comes from another radio in the room on 445.850 MHz, playing
what the Pluto sends there; the module tunes to 445.850 to wait for that carrier, then back to
its own quiet channel to measure. Per state and pass:

- PA11's converter on the AF pin at each of REG_47's sixteen output selects,
- GP0's line (PB1) at each of REG_34's sixteen output selects,
- every register 0x00-0x7F, the least and most of six reads,
- REG_64 and REG_6F read as fast as they come, and PA0.

Run micp2a, 2026-11-20 02:38 UTC (the K6 on 440.850 MHz, keying nothing):

**The AF pin in receive (state 0), no signal.** In-band rms of full scale, quiet pass / sound pass:

| Select | | Select | |
| --- | --- | --- | --- |
| 0 (mute) | 0.0002 / 0.0002 | 8 | 0.178 / 0.179 |
| 1 (received audio) | 0.076 / 0.065 | 9 | 0.091 / 0.073 |
| 2 | 0.0002 / 0.0002 | 10 | 0.0002 / 0.0002 |
| 3 | 0.079 / 0.088 | 11 | 0.079 / 0.079 |
| 4 | 0.026 / 0.026 | 12 | 0.079 / 0.070 |
| 5 | 0.026 / 0.027 | 13 | 0.102 / 0.099 |
| 6 | 0.078 / 0.045 | 14 | 0.081 / 0.075 |
| 7 | 0.040 / 0.026 | 15 | 0.0007 / 0.0008 |

Twelve selects carry the receiver's noise in one form or another, and none of them cares
about the sound in the room. State 1 (the mic ADC on) reads the same.

**State 2 (the mic ADC and the TX DSP on) silences the AF pin**: all sixteen selects sit at
the converter's floor (0.0002) in the quiet pass. In the sound pass fifteen still do, and
**select 4 reads 0.0026, with its energy at 1 kHz and 3 kHz**: the mic's audio, by way of the
transmit side's audio chain (the 3 kHz is the small speaker's third harmonic, lifted by the
pre-emphasis), on the AF pin where PA11 reads it.

Nothing else followed the sound:

- GP0: selects 9 and 11 wander by themselves in every state, 12 to 15 are bit streams whose
  density does not move at 1 kHz, the rest are constant.
- Registers: the meters at 0x61-0x6F differ between the passes in state 0 as much as in the
  others (the retune between the passes moves them), REG_64 (the VOX level) reads 8 and then
  4 in every state, and nothing reads fast enough to matter.
- PA0 stays at 0 to 2 counts.

### micpin: the hit, looked at properly

`c/k6/mod/micpin.c` holds state 2 and select 4 and takes whole captures. Run micp3a,
2026-11-20 02:47 UTC, the same tone from the same radio:

| Capture | 300-3000 Hz rms | 1 kHz | 3 kHz | |
| --- | --- | --- | --- | --- |
| Q: the room quiet | 0.00054 | | | room noise: three times the converter's floor |
| a: select 4, gains as found | 0.0023 | 0.0019 | 0.0018 | also 2, 4 and 5 kHz, and 120, 240, 360 Hz |
| b: REG_48's AF gain 2 at its most | 0.0030 | 0.0027 | 0.0020 | +2 dB |
| c: and REG_7D's mic sensitivity at its most | 0.0026 | 0.0022 | 0.0018 | no more |
| d: as c, drive time 2 | 0.0029 | 0.0023 | 0.0019 | |
| e: as c, select 5 | 0.00017 | | | the floor: select 4 only |
| f: as c, the TX DSP off again | 0.035 | | | the receiver's noise is back and covers it |
| L: as c, 0.72 s at 9 kHz | 0.0034 | 0.0025 | 0.0014 | the first one listened to |

Four separate captures carry the tone at 1,000 Hz with its harmonic series; the 120 Hz series
is mains hum from the monitoring radio's speaker, which the mic heard too. The quiet capture
and select 5 carry neither. So:

- **The recipe:** REG_30 bits 2 and 1 set (the mic ADC and the TX DSP) with the chip otherwise
  in receive, REG_47's select at 4, the amplifier off, PA11 as the converter.
- **The TX DSP is needed.** With the mic ADC alone, select 4 carries the receiver's own
  baseband noise. With the TX DSP on the receive audio stops and the transmit side's audio
  (mic, pre-emphasis, its filters) takes its place on that select.
- **The level:** the whole mic signal is 0.0034 of full scale against a floor of 0.00017, 26 dB,
  for a small speaker a foot away. The AF gain register gives 2 dB and the mic sensitivity
  register nothing (the transmit side has its own gain control).

### micrec: three seconds of speech

`c/k6/mod/micrec.c` records as long as the scratch allows: the converter at 200 turns a
sample (8 kHz), each sample less its running mean, times 8, as 4-bit IMA ADPCM (25,584
samples, 3.2 s, in 12.8 KB), then dumped. `tools/k6_micprobe.py --out DIR` decodes it and
writes two WAVs: as recorded, and with the pre-emphasis undone.

Run micp4a, 2026-11-20 02:56 UTC: the Pluto sends a 12 s voice clip (a LibriSpeech reader) on
445.850 MHz, and the monitoring radio's speaker is a foot from the K6. 25,584 samples at
7,954 Hz, none clipped. The recording's loudness follows the clip's from 1.55 s in, which is
where the recording began (envelope correlation 0.62), but the speech stands only about 9 dB
over a steady floor: room noise, and the monitoring radio's hum at 120, 240 and 360 Hz. That
floor is acoustic. The quiet room already reads three times the converter's own noise, and
the mic is made for a mouth an inch away, not a speaker a foot away.

## What a product needs from here

- **The converter on a timer interrupt.** As a module it takes the whole CPU while it runs
  (constant-time bit-banging), so a recording is as long as the RAM that holds it. A sotto
  encoder beside it needs the loop in the firmware, on a timer: about 75 CPU cycles a turn at
  200 kHz is 16 % of the CPU and, by the first-order rule, still some 40 dB in a 3 kHz band.
  The slim sotto 1200 encoder takes 27 % (sotto's `docs/slim-encoder.md`).
- **More level.** The converter's full scale is set by the charge of its two pulses (6 CPU
  cycles through 4.7k). Shorter pulses, or the pin's 40k pull resistors in place of the
  pull-down's drive, make the same audio a larger share of full scale. Untried.
- **The pre-emphasis undone** (one pole, in the encoder's front end) and the receive audio's
  return when the mic bits are cleared, which the core's retune already does.
- **API level 14 on the radio**, so that the bits go through `fd_write`.

Whatever becomes of the mic, received audio is within the MCU's reach by the same pin: the
slim encoder can also be fed from a voice the K6 hears on FM.
