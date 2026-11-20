# Can mic audio reach the K6's MCU?

Jeff asked on 2026-11-19 for a slim sotto 1200 encoder on the K6 (sotto's `docs/slim-encoder.md`)
and, once it fitted, chose "mic probe first": find out whether the microphone's audio can reach
the MCU with no hardware change, before anything is built on it. `docs/k6-fd6818-audio.md` had
the standing answer, "no": the mic goes to the FD6818's MICIN pin, and the chip hands the MCU
meters and decoder results, never samples.

This page is the probe: what was tried, what the radio said, and the one rule it needed. It
also holds a claim that was made and withdrawn the same evening ("the chip can put the mic on
that pin"), with the runs that took it down, because the way it went wrong is worth keeping.

## Short answer

- **The MCU has an audio input after all: the beep pin.** PA11, the voice prompts' PWM pin,
  shares a node with the FD6818's audio output. Run as a one-pin delta-sigma converter
  (`c/k6/mod/pa11adc.h`) it digitises whatever the chip puts on its AF pin, with no part added:
  the receiver's hiss reads 50 dB above the converter's own noise in 300-3000 Hz, and a 1 kHz
  tone sent to the K6 comes back at 39 to 41 dB SINAD.
- **The VOX pin is a dead end.** PA0 sits near 0 V in receive and shows neither a waveform nor
  a loudness.
- **The mic does not reach the MCU in receive.** With the mic ADC and the TX DSP switched on
  while the K6 receives (REG_30 bits 2 and 1; the PA off, nothing keyed), no AF output select,
  no GP0 select and no register carries the mic. For an hour select 4 looked like it did: a
  tone and a voice "played at the mic" came back on it. They had come by radio. That select is
  the receiver's raw baseband, and the Pluto that fed the room's speaker was leaking into it
  ("The correction", below; Jeff's ear caught it: "sounds like mic audio is mixed with fm
  audio").
- **Nor keyed, nor by the guide's "DISC baseband" mode.** Keyed with the PA off, every AF
  select is silent with the mic ADC on. REG_44 bit 0, where the FD6818 guide switches the mic
  pin into the receive audio chain, changes nothing on this chip. So the standing answer holds:
  **the K6's mic cannot reach the MCU without a hardware change.** The last runs were made
  again with Jeff at the radio hearing the tone (micr2a, "The repeat with a listener", below).
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

The level outlives its first purpose. The mic is on none of these bits' selects, and Jeff kept
the level for the baseband tap (2026-11-19: "Keep baseband tap"): bit 1, the TX DSP, in
receive, with REG_47 select 4 ("The correction", below).

## Probe 2: the mic ADC on, with no flash

(Read this section with "The correction" after it: the measurements stand, the reading of
select 4 as the mic does not. Every "sound" pass here had the Pluto on the air on 445.850 MHz.)

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
**select 4 reads 0.0026, with its energy at 1 kHz and 3 kHz**. This was read as the mic's audio
by way of the transmit side's audio chain. It was the Pluto's signal (below).

Nothing else followed the sound, or the Pluto:

- GP0: selects 9 and 11 wander by themselves in every state, 12 to 15 are bit streams whose
  density does not move at 1 kHz, the rest are constant.
- Registers: the meters at 0x61-0x6F differ between the passes in state 0 as much as in the
  others (the retune between the passes moves them), REG_64 (the VOX level) reads 8 and then
  4 in every state, and nothing reads fast enough to matter.
- PA0 stays at 0 to 2 counts.

### micpin: the hit, looked at more closely (and still misread)

`c/k6/mod/micpin.c` holds state 2 and select 4 and takes whole captures. Run micp3a,
2026-11-20 02:47 UTC, the same tone from the same radio:

| Capture | 300-3000 Hz rms | 1 kHz | 3 kHz | |
| --- | --- | --- | --- | --- |
| Q: the room quiet, the Pluto off | 0.00054 | | | three times the converter's floor |
| a: select 4, gains as found | 0.0023 | 0.0019 | 0.0018 | also 2, 4 and 5 kHz, and 120, 240, 360 Hz |
| b: REG_48's AF gain 2 at its most | 0.0030 | 0.0027 | 0.0020 | +2 dB |
| c: and REG_7D's mic sensitivity at its most | 0.0026 | 0.0022 | 0.0018 | no more |
| d: as c, drive time 2 | 0.0029 | 0.0023 | 0.0019 | |
| e: as c, select 5 | 0.00017 | | | the floor: select 4 only |
| f: as c, the TX DSP off again | 0.035 | | | the receiver's noise is back and covers it |
| L: as c, 0.72 s at 9 kHz | 0.0034 | 0.0025 | 0.0014 | the first one listened to |

Four separate captures carry the tone at 1,000 Hz with harmonics and a 120 Hz series; the quiet
capture and select 5 carry neither. At the time this read as a recipe for the mic (state 2,
select 4), with the 120 Hz series as hum from the monitoring radio's speaker and the harmonics
as that speaker's distortion. What the table does show:

- with the mic ADC alone, select 4 carries the receiver's noise; with the TX DSP on as well the
  receive audio stops and select 4 goes quiet until a strong signal is on the air;
- neither the AF gain register nor the mic sensitivity register moves the level by much, which
  was a hint: a mic signal would have followed REG_7D.

### micrec: three seconds of "speech"

`c/k6/mod/micrec.c` records as long as the scratch allows: the converter at 200 turns a
sample (8 kHz), each sample less its running mean, times 8, as 4-bit IMA ADPCM (25,584
samples, 3.2 s, in 12.8 KB), then dumped. `tools/k6_micprobe.py --out DIR` decodes it and
writes two WAVs: as recorded, and with the pre-emphasis undone.

Run micp4a, 2026-11-20 02:56 UTC: the Pluto sends a 12 s voice clip (a LibriSpeech reader) on
445.850 MHz, and the monitoring radio's speaker is a foot from the K6. 25,584 samples at
7,954 Hz, none clipped. The recording's loudness follows the clip's from 1.55 s in, which is
where the recording began (envelope correlation 0.62), with the speech only a few dB over a
steady floor. Jeff listened: "sounds like mic audio is mixed with fm audio".

Run micp4b, 03:03 UTC, the speaker moved to an inch from the mic: no louder. Above 600 Hz the
two recordings are the same size within 1 dB, the 120 Hz line doubled, and the match to the
clip fell to 0.26. A voice twelve times closer should have come up about 20 dB.

## The correction: it came by radio

`c/k6/mod/miclvl.c` holds the same state and select and takes one 84 ms capture a second for
48 s, in rotation: the route as above, the mic ADC off (the TX DSP alone), and REG_19's bit 15
flipped (the chip's mic AGC, on in 0x1041). Four runs, 2026-11-20 03:17 to 03:34 UTC:

**micp5d: a 1 kHz tone at the mic in steps of loudness** (the Pluto on 445.850 MHz at 3,000,
1,500, 750, 375 Hz deviation, then carrier only; the monitoring radio an inch from the mic).
Of full scale:

| Step | 1 kHz | 2 kHz | 3 kHz | 119 Hz |
| --- | --- | --- | --- | --- |
| full | 0.0020 | 0.0002 | 0.0018 | 0.0057 |
| half | 0.0003 | 0.0027 | 0.0004 | 0.0055 |
| quarter | 0.0037 | 0.0006 | 0.0002 | 0.0060 |
| eighth | 0.0014 | 0.0002 | 0.0001 | 0.0057 |
| carrier only | 0.0003 | 0.0003 | 0.0002 | 0.0058 |

The level does not follow the loudness: at half the fundamental is gone and the second
harmonic stands alone. No speaker does that. An FM signal's envelope does (Bessel terms of the
deviation, odd or even by the phase the modulation happens to stop at). The mic ADC bit and
the AGC bit change nothing in any step, and the 119 Hz series is there whenever the Pluto's
carrier is, tone or no tone.

**micp5e: Jeff's test, a 700 Hz tone on the K6's own channel** (the Pluto on 440.850 MHz, -71
dBm, nothing playing in the room). The carrier alone puts a line at 1,214 Hz on select 4,
0.010 of full scale: the carrier itself, shifted down by the difference between the Pluto's
frequency and the chip's. With the tone on, the line grows sidebands 700 Hz apart (524, 1,917,
2,619 Hz). **Select 4 with the TX DSP on is the receiver's baseband before any demodulator**,
which is what the BK4819's register notes call selects 4 and 5 ("baseband", for SSB).

**micp5f: the control, the stepped tone on 446.850 MHz**, a channel no radio in the room
listens to, so nothing plays. The K6 records the same table as micp5d, step for step (1 kHz
0.0021, 0.0004, 0.0036, 0.0015; the same 119 Hz series). The sound was never needed.

**micp5g: the Pluto's power stepped** (the quarter-level tone on 445.850 MHz at gain -30, -38,
-46 dB; the room's tone the same each time, since an FM receiver's volume does not follow the
signal's strength). The 119 Hz line falls from 0.0053 to 0.0008 for 8 dB less power, 16 dB,
and is gone at 16 dB less: a second-order leak, 2 dB for each dB. The tone goes with it. With
the leak gone and the speaker still an inch from the mic, no steady tone is left above 0.0004
of full scale (receive audio on the same pin: 0.06 to 0.09).

So the "mic route" was this: the Pluto's transmission is not pure FM (its carrier leak beats
with the FM signal, and it carries a 119 Hz ripple), a signal 5 or 6 MHz off the K6's channel
at -61 dBm reaches the chip's mixer, the mixer's second-order term turns that signal's envelope
into baseband, and select 4 hands the baseband to PA11. Every "sound" test had the Pluto on
the air, and none had the Pluto on the air without the sound until micp5f.

Three things to keep:

- **A sound source fed by a transmitter needs its control**: the transmitter on with no sound
  (another channel), or the sound with no transmitter. At gain -46 the Pluto still drives the
  room's radio and leaks nothing the converter can see: that is the clean way to play a sound
  at the K6.
- **The converter has a 1 kHz line of its own**, 0.0005 to 0.0012 of full scale with a second
  harmonic, there with no signal at all (most likely the firmware's 1 ms interrupt stretching
  one turn in every thousand or so). Test tones should sit elsewhere, and a firmware version of
  the converter on its own timer would not have it.
- **A wake carrier before the upload**: the K6 leaves its upload screen after 60 s and its
  EXPLORE screen can be deaf to the bench channel for a minute at a time (it was, for the first
  try at micp5d). The jobs since send 65 s of Morse ID on 440.850 MHz first.

## Two more tries, with a clean sound

Both use the Pluto at gain -46 dB to play a 1,300 Hz tone through the room's radio (no leak,
and clear of the converter's own 1 kHz line), and take the same captures with the room quiet.

**Nobody heard the tone in these two runs**, so they were repeated with a listener (below). The K6 read the Pluto's carrier on 445.850 MHz at -76 dBm both times, far above a
squelch, so the room's radio very likely played it. But whether it did, how loud, and where its
speaker sat at 03:44 and 03:48 UTC is not known (Jeff, asked afterwards: "I don't know if it
did. We should check again later"). The same holds for the receive sweep of probe 2, whose
only sounding passes had the leaking Pluto as their source. A negative here is only as good as
the sound at the mic.

**micdisc: the guide's DISC baseband mode** (run micp6a, 03:41 UTC). The FD6818 guide has a
mode for sets whose receiver is another chip: that receiver's discriminator goes into MICIN and
the chip runs it through its receive audio chain to AFOUT, switched on by REG_44 bit 0 with the
mic ADC beside the RX DSP. If this chip had it, the mic would come out of the AF pin like
received audio. `c/k6/mod/micdisc.c` flips the bit (0x8ED8 to 0x8ED9) and reads all sixteen
selects with the mic ADC on, and three with the TX DSP on too. The table is the receive table
of probe 2 again: the receiver's hiss on select 1 (0.062 of full scale) whether the bit is set
or not, the same twelve noisy selects, no 1,300 Hz anywhere. The bit does something else here,
or nothing (the guide describes chip ID 0x6818; this one reads 0x4819).

**mickey: keyed** (run micp7a, 03:45 UTC; Jeff chose "one keyed probe, then stop").
`c/k6/mod/mickey.c` keys up on 440.850 MHz (`tx_on`, the core's guards), sends AG7EW in CW with
the PA, then with the PA off and the chip still in its transmit state (REG_30 = 0xC1FA) sets
the AF DAC and mic ADC bits (0xC3FE, over the bus pins; never with the PA on, so the mic is
never on the air) and reads the sixteen selects, then four with the mic ADC off. Twenty
captures quiet and twenty with the tone: all forty sit at the converter's floor, 0.00014 to
0.00024 of full scale, with nothing at 1,300 Hz above 0.00007. Keyed, the chip puts nothing on
its AF pin at all.

The module was then given the rule every keying module has, to read the channel before each
key-up and refuse a busy one (-4), and run again unattended (micp8a, 04:24 UTC): both key-ups
went ahead on the quiet channel and the forty captures read the same floor (0.00014 to
0.00023).

## The repeat with a listener

Run micr2a, 2026-11-20 05:41 to 06:17 UTC. Jeff at the bench, his radio on 445.850 MHz with
its speaker at the K6's mic hole. The job armed first and waited: `c/k6/mod/keygo.c` showed
ARMED on the K6 and the job sent nothing until Jeff pressed a key (06:04:07 UTC). Then the
Pluto played the 1,300 Hz tone at gain -46 for three minutes ("Tone is indeed being sent"),
and the three runs followed, each with the same tone: micprobe (the receive sweep, states 0 to
2, all sixteen selects), micdisc and mickey. All loaded and ran (returns 6, 44, 40).

No capture of the 180 has a 1,300 Hz line. Each capture's fit at 1,300 Hz was set against the
median fit at thirty frequencies around it: the median ratio is 1.1 and the largest 3.3, in a
quiet capture as much as a sounding one, which is what 180 captures of noise give. The silent
selects stay at the converter's floor (0.00014 to 0.00024 of full scale) and the hissy ones at
the receiver's hiss, tone or no tone. Keyed, all forty captures sit at the floor again.

So the answer is measured: **the K6's mic does not reach the MCU in receive, keyed, or with the
DISC bit, with a tone that a listener heard at the mic.**

## Where this leaves the K6

- **The mic:** no path to the MCU. In receive, keyed, with the mic ADC and the TX DSP on or
  off, and with the guide's DISC bit: no AF select, GP0 select or register carries it. What is
  left is hardware (a wire from the mic to an ADC pin), which Jeff has ruled out. The repeat
  with a listener (micr2a) confirmed it. The probe stops here.
- **Received audio is within the MCU's reach** by the beep pin: the slim sotto 1200 encoder
  (27 % of the CPU, sotto's `docs/slim-encoder.md`) can be fed from a voice the K6 hears on FM.
  For that the converter belongs on a timer interrupt in the firmware: as a module it takes the
  whole CPU while it runs (constant-time bit-banging), so a recording is as long as the RAM
  that holds it. About 75 CPU cycles a turn at 200 kHz is 16 % of the CPU and, by the
  first-order rule, still some 40 dB in a 3 kHz band.
- **The baseband select is a find of its own**: with the TX DSP bit on, select 4 gives the MCU
  the channel before the FM demodulator, as audio (a carrier 1.2 kHz off tune is a 1.2 kHz
  tone). That is a second way to a receiver in software, beside GP0's 1-bit stream
  (`docs/k6-i2s-probe.md`), and it needs API level 14 or the bus-pin write. Jeff kept level 14
  for it.
